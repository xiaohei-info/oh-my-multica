---
name: software-design-lifecycle-tech-overview-system-arch-diagramming
description: "Use when an architect in overview technical design must produce or review the 系统架构图 and needs the fully merged system-architecture specialist method directly inside the new lifecycle family."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, technical-design, overview-design, system-architecture]
    related_skills: [arch-lifecycle-tech-overview-methodology, arch-lifecycle-tech-overview-functional-arch-diagramming]
---

# Technical Overview System Architecture Diagramming

## Overview

This skill now directly contains the merged specialist doctrine that previously lived in the legacy system-architecture skill.

## Integrated Legacy Specialist Doctrine (Preserved and Merged)

# System Architecture Diagramming

## Overview

This is the architect-private skill for drawing and reviewing **系统架构图** in the **概要设计** stage.

This skill intentionally preserves the **full drawing-detail layer** from the system-architecture-diagram spec instead of compressing it away. It is meant to strengthen architect execution, not merely restate high-level principles.

Its role in the architecture lifecycle is anchored in the local source note `软件架构设计生命周期`:
- **功能架构图** 负责把设计层、运行层、数据底座与运营支撑拆开。
- **系统架构图** 负责交代内部子系统、外部依赖、通信关系与边界条件。
- **技术架构图** 负责把系统进一步压到具体技术栈、引擎、中间件、数据存储与执行路径。

Core principle:
**系统架构图 belongs to overview design, so it should explain subsystem structure, dependency boundaries, and collaboration relationships — not merely list functions, not collapse into流程时序, and not yet dive into full technical-stack implementation detail.**

This skill exists because many so-called “系统架构图” fail in one of four ways:
- they are really **功能清单/功能分层图**, so they say what the system should do but not what subsystems exist or how boundaries are controlled
- they are really **流程图/时序图**, so they show time order instead of stable structure
- they are really **技术架构图**, so they jump too early into concrete middleware, engine, host, container, or code-carrier detail
- they throw away the detailed visual grammar, so future diagrams lose consistency in colors, shapes, boundaries, and line semantics

Use this skill to produce a diagram that is abstract enough for 概要设计, but detailed enough in drawing grammar to guide later technical architecture, interface design, deployment design, and review.

## When to Use

Use when:
- the user asks for 系统架构图、概要设计中的系统架构图、子系统架构图、系统边界图
- you need one picture to explain what lives inside the system, what stays outside, and how the two sides collaborate
- a design doc must show internal subsystem decomposition plus external dependencies
- the team is mixing 功能视角, 系统视角, 技术视角, and 流程视角 and needs a clean separation
- you need to review whether an existing “架构图” is actually a proper system-architecture artifact
- you need the full visual spec preserved: primitive vocabulary, color semantics, shape rules, line semantics, boundary rules, layout modes, anti-pattern checks, and tool adaptation

Do not use when:
- the main question is “系统要具备哪些功能、如何分层” → use `arch-lifecycle-tech-overview-functional-arch-diagramming`
- the main question is “业务从谁到谁如何流转” → use business solution / process flow artifacts
- the main question is “具体用什么技术栈、中间件、引擎、存储产品、运行载体” → use technical architecture
- the main question is “请求按时间顺序如何执行、回调、重试、补偿” → use sequence/state/flow artifacts
- the main question is “机房、VPC、K8s、节点、网络拓扑” → use deployment / physical architecture
- the main question is “领域语义、限界上下文、聚合” → pair with `ddd-domain-modeling-for-architecture`

## What A System Architecture Diagram Must Answer

At 概要设计 stage, the system architecture diagram is the direct answer to this lifecycle design question:
- **内部子系统、外部依赖、通信关系与边界条件是什么？**

A qualified diagram should let a reviewer infer within ~30 seconds:
1. what the system-of-interest is
2. what major subsystems or major runtime-responsibility blocks exist inside it
3. which systems/services/data providers are external dependencies
4. how internal and external parties communicate
5. what access boundaries or control chokepoints exist
6. what the main collaboration spine is
7. what is intentionally isolated, optional, sidecar, asynchronous, or weakly coupled

If the viewer still cannot tell “inside vs outside” or “who can talk to whom and through what”, the diagram is not finished.

## Boundary With Neighboring Artifacts

Keep the system architecture diagram separate from nearby artifacts.

### System architecture vs functional architecture
- **Functional architecture**: what capabilities/functions exist, and how they are layered
- **System architecture**: what subsystems exist, where the system boundary is, what is external, and how those parts collaborate

Fast judgment rule:
- if the main nouns are **能力/模块/功能域/支撑域**, you are probably in functional architecture
- if the main nouns are **子系统/服务/平台/外部系统/网关/通道/存储/依赖域**, you are probably in system architecture

### System architecture vs technical architecture
- **System architecture**: responsibility-level decomposition and collaboration structure
- **Technical architecture**: concrete tech realization such as Kafka / Redis / MySQL / ES / Flowable / K8s / Workers / Lambda / VM / sidecar runtime

Rule:
A system architecture diagram may name a gateway, event bus, scheduler, store, or platform block **when they are structurally necessary**, but it should stop at the level of subsystem/carrier role, not product-deployment minutiae unless that detail is architecturally decisive.

### System architecture vs process / sequence / state diagrams
- **System architecture** is stable structure
- **Process / sequence / state** artifacts are dynamic behavior over time

Important review rule:
**If the current “系统架构图” mainly shows step order, decision branches, retries, or time-sequenced request arrows, it is actually a flow artifact, not a system architecture artifact.**

### System architecture vs deployment / physical topology
- **System architecture**: logical runtime collaboration and controlled boundaries
- **Deployment / physical topology**: where the system runs physically, on which networks/zones/nodes/clusters

A clean overview design often needs both, but they should not be merged into one overloaded picture.

## The Distinction From 功能架构图 Must Be Explicit

When both diagrams are present in the same overview-design document, use this split.

### 功能架构图 should answer
- 系统为什么而建
- 系统要具备哪些功能
- 功能如何分层
- 哪些功能是核心闭环、哪些是支撑治理

### 系统架构图 should answer
- 这些功能最终沉到哪些子系统/服务/平台单元
- 哪些在系统边界内，哪些在边界外
- 内外部如何通信与协作
- 哪些依赖是同步、异步、旁路、弱依赖或受控接入
- 关键边界条件与隔离关系是什么

### Practical conversion rule
A useful way to move from functional architecture to system architecture is:
1. keep the major capability groups
2. convert them into owned subsystem-level responsibility units
3. separate external collaborators from internal owners
4. insert real communication chokepoints
5. make boundary and dependency direction explicit

### Smell test
If replacing subsystem names with capability labels leaves the picture almost unchanged, then your “system architecture” is probably still just functional architecture in disguise.

---

## 1. 核心原则（必须体现）

所有系统架构图必须回答四个问题：
1. 系统边界在哪？（什么是内部，什么是外部）
2. 内部有什么模块？（子系统、服务、组件、存储）
3. 模块之间如何协作？（同步/异步、强/弱依赖、数据流向）
4. 外部依赖谁？（第三方、基础设施、开放平台）

设计原则在图中的转译：
- **低耦合**：子域间只通过标准协议（Channel）相连，禁止 Node 直接穿透边界互调；子域用虚线框物理隔离。
- **无侵入**：旁路能力（Agent/Plugin/Sidecar/Interceptor）使用小尺寸节点，挂载在主链路侧面，连线用**虚线箭头**。
- **隔离性**：多租户、多环境、多规则集使用独立虚线子域框，同类节点多实例用 `×N` 或并列表示。
- **灵活性**：简单配置入口与自定义扩展入口并置，配置面节点（芽绿色）与执行面节点（天蓝色）区分绘制。
- **易用性**：控制/配置面节点面积适当放大，或置于图中最显眼位置（左上或顶部中央）。

Architect interpretation at overview-design stage:
- these are not optional style notes; they are how system-level design intent becomes visible
- if the user explicitly requests low coupling / sidecar / tenant isolation / governance-vs-execution split, that request must appear in the visual structure, not only in prose

---

## 2. 抽象图元（唯一词汇表）

禁止在图中使用任何业务名词作为图元类型。所有实体必须映射为以下五种抽象图元之一：

- **Actor**
  - 形状：圆角矩形
  - 职责：外部触发者：用户、客户端、上游系统、第三方服务、开放 API 消费者、设备
  - 基础色（HEX）：`#E8F5E9`（薄荷绿）
  - 使用条件：任何与系统交互的外部实体

- **Node**
  - 形状：圆角矩形
  - 职责：系统内部功能实体：服务、模块、组件、应用、子系统、函数、处理器
  - 基础色（HEX）：`#E3F2FD`（天蓝）
  - 使用条件：系统边界内的核心功能单元

- **Store**
  - 形状：圆角矩形底部加波浪线/圆柱形
  - 职责：数据持久化：数据库、缓存、文件系统、对象存储、搜索引擎、日志存储、消息中间件
  - 基础色（HEX）：`#ECEFF1`（灰蓝）
  - 使用条件：任何数据留存或状态保持设施

- **Channel**
  - 形状：圆角矩形或菱形
  - 职责：通信与集成设施：消息队列、事件总线、API 网关、负载均衡、服务网格、配置中心、定时任务调度
  - 基础色（HEX）：`#FFF8E1`（浅黄）
  - 使用条件：任何中转、路由、广播、集成设施

- **Boundary**
  - 形状：虚线/实线/点划线矩形框
  - 职责：分组与隔离：系统边界、子域、安全域、部署单元、租户隔离区、逻辑分层
  - 边框色：`#90A4AE`
  - 填充：透明或 `#FAFAFA`（5%透明度）
  - 使用条件：任何需要表达“范围”或“分组”的场景

> **最小集原则**：简单系统仅使用 Actor(绿) + Node(蓝) + Store(灰) + Channel(黄) + Boundary(框) 即可成图。复杂系统按需启用扩展色。

### 扩展色（按需启用）

- **控制/配置面**
  - 色值：`#DCEDC8`（芽绿）
  - 启用条件：存在独立的管理后台、配置中心、规则设计器、可视化平台

- **展现/交互面**
  - 色值：`#F3E5F5`（浅紫）
  - 启用条件：存在独立的前端应用、用户门户、管理界面、可视化界面

- **运营/观测面**
  - 色值：`#FFF3E0`（暖橙）
  - 启用条件：需要独立突出监控、告警、审计、报表、通知中心

- **核心/关键路径**
  - 色值：`#BBDEFB`（深蓝）
  - 启用条件：需要在一个图中突出主链路节点，与次要节点区分

- **数据/业务数据**
  - 色值：`#C8E6C9`（翠绿）
  - 启用条件：需要区分“业务状态数据”与“基础设施/日志数据”时

### Architect-specific interpretation of the five primitives

Before drawing, classify candidate content into five buckets:
1. **Actors / external participants**
2. **Internal subsystems / core nodes**
3. **Stores / state carriers**
4. **Channels / integration chokepoints**
5. **Boundaries**

This classification prevents three common mistakes:
- drawing every thing as the same kind of box
- forgetting that channels/gateways are different from owned business subsystems
- mixing external actors directly into the internal system body

---

## 3. 形状与文字规范

### 3.1 节点形状
- **统一使用圆角矩形**，圆角半径约为节点高度的 15%–20%。
- **Actor** 可使用圆角矩形或 stickman（人形图标），但同一图中必须统一。
- **Store** 必须在标准矩形基础上增加底部波浪线或简化为圆柱形，以 visually 区分于 Node。
- **Channel** 可使用标准圆角矩形，但面积应略小于同级 Node（暗示中转/薄层）。

### 3.2 文字
- **节点内文字**：黑色 `#212121`，水平垂直居中，字号占节点高度的 25%–30%。
- **连线标注**：黑色 `#424242`，置于连线上方或右侧，字号比节点文字小 20%。
- **分组/边界标题**：加粗 `#37474F`，置于边界框左上角或顶部中央。
- **文字间距**：节点内文字与四边框保留至少 10px 内边距；多行文本行高 1.4。

### 3.3 线条与箭头
线条语义**取决于当前图类型**，必须在图的角落用文字声明图类型（如 "图类型：逻辑架构图"）。

- **逻辑架构图 / C4 Container**
  - 实线箭头（—>）：同步调用、强依赖、主链路请求
  - 虚线箭头（-.->）：异步事件、配置下发、旁路通知、弱依赖、心跳
  - 无箭头实线（—）：包含关系、分组归属、层级边界

- **数据流图 (DFD)**
  - 实线箭头（—>）：数据流向、ETL 管道、主数据流
  - 虚线箭头（-.->）：事件流、CDC、广播、缓存失效
  - 无箭头实线（—）：数据存储关联、同构副本、分区关系

- **部署拓扑图**
  - 实线箭头（—>）：网络可达、物理连接、同区通信
  - 虚线箭头（-.->）：跨区/跨云连接、VPN、公网链路、不可靠连接
  - 无箭头实线（—）：安全域边界、VPC 划分、机房边界

- **交互序列图**
  - 实线箭头（—>）：请求/响应消息
  - 虚线箭头（-.->）：回调、广播、心跳、异步通知
  - 无箭头实线（—）：生命线、激活条、参与者边界

补充规则：
- **连线粗细**：主链路 2px，旁路/弱依赖 1px。
- **连线标注**：**所有跨 Boundary 的连线必须在中段标注协议或数据格式**（如 `HTTPS` `gRPC` `Kafka` `SQL` `JSON` `Thrift` `MQTT`），禁止出现“裸线”。
- **双向关系**：禁止绘制双向箭头，应拆分为两条单向线，或明确标注 `Request` / `Response`。

Architect review rule:
- if a cross-boundary edge is architecturally meaningful but unlabeled, assume the diagram is underspecified
- if too many line styles exist without a declared type legend, the diagram is semantically unstable

---

## 4. 布局模式（根据系统结构本质选择）

单张图**只应使用一种主导布局模式**。若系统同时存在多种结构本质，拆分为多张图。

- **Layered（分层蛋糕）**
  - 适用系统本质：传统单体、分层架构、前后端分离、审批引擎子系统
  - 视觉特征：严格水平多层，Node 按层水平对齐，层间留白 ≥ 节点高度的 1.2 倍。自上而下通常为：展现 → 业务逻辑 → 数据 → 基础设施。

- **Hub-Spoke（中心辐射）**
  - 适用系统本质：中台、网关、BFF、API 聚合层、认证中心
  - 视觉特征：中心一个大 Node（Hub），四周 Actor/Node 指向它；Channel 位于中心与外围之间。

- **Pipeline（管道流）**
  - 适用系统本质：ETL、流计算、CI/CD、编解码链、数据管道
  - 视觉特征：左→右长链，Node 按处理顺序排布；Store 位于节点下方或旁路；Channel 用于旁路广播。

- **Mesh（网络网格）**
  - 适用系统本质：微服务、去中心化、P2P、服务网格、事件驱动
  - 视觉特征：Node 均匀分布，连线呈网状；强调彼此多对多关系；Channel 位于网中央作为事件总线。

- **Star-Plugin（星型插件）**
  - 适用系统本质：插件化平台、扩展框架、OpenAPI 生态、规则引擎
  - 视觉特征：中心内核（小面积、深色），外围插件 Node 环绕；内核与插件通过 Channel 连接。

- **Symmetric（对称双活）**
  - 适用系统本质：高可用、灾备、多活架构、主从复制
  - 视觉特征：左右或上下对称复制，中间用 Channel 同步；可用不同颜色区分主/备或不同可用区。

**组合规则**：复杂系统先用 Hub-Spoke / Mesh 描述整体（Type-A：分布式联动），再对关键子系统用 Layered 展开内部（Type-B：分层内构）。

### Recommended default selection heuristic for overview-stage 系统架构图

Choose the layout by the system’s real structural question:
- if one center mediates most interactions → **Hub-Spoke**
- if one top-down request path dominates → **Layered**
- if many internal peers collaborate without one obvious center → **Mesh**
- if the system is fundamentally a staged processing chain → **Pipeline**
- if the system is plugin/extension centered → **Star-Plugin**
- if the system is multi-AZ/dual-active symmetry-dominant → **Symmetric**, usually better paired with deployment view

Review warning:
If the chosen layout makes the system boundary or communication control harder to read, it is the wrong layout even if it looks visually impressive.

---

## 5. 边界与分组规范

边界是架构图的灵魂，必须精确表达：

- **系统边界**
  - 视觉表达：粗实线矩形（线宽 3px），左上角加粗标注系统名
  - 语义：当前正在设计的系统范围；框内是“我们负责开发和维护的代码”。

- **子域边界**
  - 视觉表达：虚线矩形（线宽 1.5px，虚线间隔 5px），浅灰背景填充（`#FAFAFA`，透明度 10%）
  - 语义：内部按业务/功能/团队划分的模块组；暗示低耦合、独立部署单元。

- **外部依赖域**
  - 视觉表达：细虚线矩形（线宽 1px），无填充，与系统边界保持 ≥ 30px 物理间距
  - 语义：第三方服务、开源组件、云厂商能力、协作方系统；暗示“不可靠的外部依赖”。

- **安全/隔离域**
  - 视觉表达：实线矩形 + 背景斜纹或轻微颜色区分（如浅红/浅绿透明度 5%）
  - 语义：DMZ、内网、生产/测试环境隔离、多租户沙箱、合规分区。

- **部署边界**
  - 视觉表达：点划线矩形（线宽 2px），底部标注环境名（如 `VPC-A` / `K8s-Prod` / `AZ-1`）
  - 语义：物理或虚拟部署单元：可用区、机房、Namespace、集群。

**关键规则**：
1. **Actor 不得画在系统边界内**。外部参与者必须位于系统边界外，通过连线穿越边界与内部交互。
2. **外部 Actor/Node 不得直接穿透系统边界与内部 Store 相连**，必须经过边界上的 Node 或 Channel，以此表达“隔离性”与“受控访问”。
3. **Store 之间禁止直接连线**。数据必须通过 Node 或 Channel 中转。

Architect review rule:
- if the reviewer cannot explain ownership from the boundaries alone, the grouping failed
- if a third-party capability is drawn inside the system merely because it is important, boundary semantics have been violated

---

## 6. 命名与粒度规则

### Node naming rule
Choose names that are:
- responsibility-oriented
- subsystem-stable
- meaningful to architects and senior engineers
- valid even if implementation classes later change

Prefer patterns like:
- 对象/域 + 子系统
- 职责 + 服务
- 管理/执行 + 平台
- 接入/适配 + 网关

Examples:
- 指令接入网关
- 任务编排子系统
- 核心处理服务
- 外部适配服务
- 审计分析子系统
- 配置管理后台

Avoid:
- abstract empty words like “平台中心” without duty
- framework names unless technically decisive
- code names only developers on one repo understand
- `OrderServiceImpl`
- `module-a`
- Java package names
- single method names
- field-level DTO names

### Same-level granularity rule
Sibling nodes should sit at roughly the same abstraction level.

Bad mix:
- API 网关
- 订单子系统
- MySQL 索引
- Kubernetes 集群
- 审计日志表

That mixes channel, subsystem, data-implementation detail, deployment detail, and schema detail.

### Store usage rule
Use stores only when persistent state materially shapes the architecture.

Good examples:
- 业务主库
- 配置库
- 搜索索引
- 对象存储
- 消息积压存储

Rule:
stores are not the center of the overview picture unless the system is fundamentally data-platform shaped.

### Channel usage rule
Use channels for controlled integration or communication facilities.

Good examples:
- API 网关
- 事件总线
- 消息队列
- 调度中心
- 服务接入层
- 配置中心

Rule:
if something’s primary job is routing / dispatch / integration / fan-out / controlled ingress, it is usually a Channel, not a Node.

### Embedded-document asset rule (live overview-design addition)

When the diagram will be consumed inside markdown/vault systems rather than as a standalone webpage only:
- do not ship only an `.html` system-architecture artifact if the overview markdown/doc is expected to embed the figure inline
- generate a companion `.svg` from the same source/output family so the overview doc can embed the diagram directly and remain readable without clicking out
- if both assets exist, the markdown/doc should normally embed the `.svg` and optionally link the richer `.html`
- when embedding raw SVG into markdown/vault documents, do not rely only on CSS class rules for text color inheritance; set explicit `fill` colors on visible `<text>` elements (titles, labels, captions) so dark-theme viewers do not silently fall back to unreadable black text

### Node naming tightening from live iteration

In overview-stage 系统架构图, prefer subsystem-duty names over page/product wording.
Examples of stronger naming moves:
- `企业 / 个人工作空间` -> `身份与空间入口`
- `企业后台 / 系统后台` -> `治理与运营控制台`
- `私聊工作台` -> `单任务会话接入`
- `群聊协作工作间` -> `多角色协作接入`

The rule behind these renames:
- system-architecture nodes should describe **owned subsystem responsibility**
- page/channel/product wording can appear in labels or descriptions, but should not dominate the primary node name when the artifact is meant to guide later subsystem design

### Cross-boundary label tightening from live iteration

When labeling cross-boundary or external-dependency edges, prefer interface semantics over vague relationship prose.
Prefer labels such as:
- `Agent API / Task Dispatch`
- `Retrieval API / Context Fetch`
- `Skill Catalog / Install Source`
- `Billing API / Connector API`

Avoid labels that are too fuzzy to guide later interface design, such as:
- `Agent runtime`
- `RAG 基座`
- `技能市场接入`
- `外部系统集成`

The goal is not protocol-level detail yet, but a clear statement of **what kind of interface/control boundary** the edge represents.

---

## 7. 反模式（禁止出现的画法）

以下画法会导致架构图产生歧义，必须禁止：

1. **裸线**：跨模块/跨边界的连线不标注协议或数据格式。
2. **越层穿透**：在 Layered 布局中，上层 Node 直接连线到下层 Store，跳过中间所有层。
3. **颜色漂移**：同一颜色在同一图中被赋予两种不同语义。
4. **Actor 入框**：外部参与者（第三方系统）被画在系统边界框内部。
5. **存储直连**：两个 Store 之间直接连线（必须通过 Node/Channel 中转）。
6. **万能单色**：所有节点使用同一种颜色，丧失角色区分。
7. **双向箭头**：在逻辑架构图中使用双向箭头（应拆分为两条单向线）。
8. **布局混合**：单张图同时使用 Layered 和 Mesh 作为主导布局，导致视觉混乱。
9. **无类型声明**：未在图角落声明图类型（逻辑架构图/数据流图/部署图），导致连线语义歧义。
10. **能力冒充子系统**：图里只有功能标签，没有真实的系统责任单元。
11. **流程冒充架构**：图主要表达时间顺序、回调、重试、状态迁移，而非稳定结构。
12. **技术细节淹没概要设计**：每个框都变成具体产品名、容器、主机、版本或源码载体。
13. **存储直连外部**：外部 Actor/外部系统直接连到内部 Store。
14. **边界缺失**：所有东西放在同一平面，内外部不可读。
15. **关系过载**：所有可能关系都画出来，形成箭头蜘蛛网。
16. **把部署图和系统图揉成一张**：VPC/节点/网络域细节淹没系统协作结构。

---

## 8. 绘制流程（AI Agent SOP）

当用户请求绘制系统架构图时，按以下步骤严格执行：

### Step 1 — 需求澄清
- 确认图类型：逻辑架构图 / 数据流图 / 部署拓扑图 / C4 Container / C4 Component？
- 确认布局模式：系统结构本质是 Layered / Hub-Spoke / Pipeline / Mesh / Star-Plugin / Symmetric？
- 确认是否需要组合：是否需要 Type-A（整体联动）+ Type-B（子系统内构）两张图？
- 确认设计原则：用户是否要求体现低耦合 / 无侵入 / 隔离性 / 灵活性 / 易用性？
- 确认文档语境：当前是在概要设计中的系统视角，而不是功能视角、技术视角或部署视角？

### Step 2 — 抽象建模
- 将用户描述的所有实体映射为 Actor / Node / Store / Channel / Boundary。
- 明确哪些属于“本系统边界内”（Node/Store/Channel），哪些属于“外部”（Actor/外部依赖）。
- 识别核心链路（用深蓝 Node 突出）与旁路能力（用芽绿小 Node + 虚线）。
- 识别哪些是受控接入点，哪些是异步/弱依赖/可选依赖。

### Step 3 — 颜色选择（最小集优先）
- 简单系统：Actor(绿) + Node(蓝) + Store(灰) + Channel(黄)。
- 复杂系统：按需启用扩展色（配置面芽绿/展现面浅紫/运营面暖橙/核心路径深蓝/业务数据翠绿）。
- 不允许颜色漂移；同色在同图只能承载一种语义。

### Step 4 — 布局与分组
- 按选定的主导布局模式排列图元。
- 绘制系统边界（粗实线）、子域边界（虚线）、外部依赖域（细虚线，保持间距）。
- 校验：Actor 是否在边界外？Store 是否不直接相连？
- 若结构本质不同，拆图，而不是在一张图里强行混排。

### Step 5 — 连线与标注
- 根据图类型定义实线/虚线语义。
- 所有跨 Boundary 连线标注协议/格式（HTTPS/gRPC/Kafka/SQL/JSON 等）。
- 体现用户要求的设计原则（见第 1 节）。
- 弱依赖、旁路、sidecar、通知链路与主链路必须 visually 区分。

### Step 6 — 工件边界校验
逐条确认：
- 这张图是否回答“内部子系统、外部依赖、通信关系、边界条件”？
- 是否误画成“系统要有哪些功能、如何分层”？若是，则它是功能架构图。
- 是否误画成时间顺序执行图？若是，则它是流程/时序图。
- 是否误画成产品/引擎/中间件堆叠图？若是，则它是技术架构图。
- 是否误画成 VPC/K8s/主机拓扑图？若是，则它是部署图。

### Step 7 — 反模式校验
逐条检查：
- [ ] 无裸线
- [ ] 无越层穿透
- [ ] 无颜色漂移
- [ ] 无 Actor 入框
- [ ] 无存储直连
- [ ] 无双向箭头
- [ ] 单图布局统一
- [ ] 图类型已声明
- [ ] 没有把功能视图、流程视图、技术视图、部署视图揉进系统视图

---

## 9. 工具适配指南

本 Skill 为元级规范，工具仅影响绘制效率，不影响规范本身。由用户或 Agent 根据文档与交付场景直接选择：

- **Excalidraw**
  - 草图评审、手绘风格、非正式沟通、快速迭代场景使用 Excalidraw。
  - 适配要点：使用“Architecture”素材库；手动匹配 5 种基础色板；边界框使用虚线手绘风格；适合 Layered / Hub-Spoke。

- **Draw.io (diagrams.net)**
  - 正式文档、可编辑源文件留存、复杂分层场景使用 Draw.io。
  - 适配要点：使用全局样式定义 5+4 色板；利用图层分离“逻辑视图”与“部署视图”；使用 Container 实现边界嵌套；导出 PNG/SVG 嵌入文档。

- **Draw.io (diagrams.net)**
  - Markdown/文档仓库沉淀、Git 版本控制下保留源文件、轻量复用、开发者友好场景使用 Draw.io。
  - 适配要点：优先保存 `.drawio` 源文件并导出 PNG/SVG；用 Container/Swimlane 表达 Boundary；通过统一样式定义颜色语义；复杂布局直接在同一工具内完成，不因工具受限而降级语义。

- **PlantUML**
  - 严谨建模、C4 模型原生支持、UML 生态场景使用 PlantUML。
  - 适配要点：直接使用 C4 扩展（`!include C4_Container.puml`）；将本 Skill 的 HEX 色值映射到 `AddElementTag`；支持所有布局模式。

- **SVG / HTML**
  - 网页嵌入、交互式架构图、动态高亮、高精度输出场景使用 SVG / HTML；当运行环境提供 SVG / HTML 制图能力时，选择对应工具。
  - 适配要点：以 CSS 变量定义色板；利用 `<g>` 标签分组实现 Boundary；SVG 对任意布局（Mesh/Star/Symmetric）支持最好；可添加 hover 提示。

Architect rule:
- tool choice may change drawing efficiency and fidelity, but it must not change semantic discipline
- if a tool cannot cleanly express the required boundary or layout semantics, switch tools instead of lowering the architecture standard

---

## 10. 加载指令（供 AI Agent 直接使用）

```text
你是一名系统架构图绘制专家。你的任务是将任何系统描述转化为清晰、专业、无歧义的系统架构图。

你必须遵守以下规范：

【工件边界】
- 系统架构图负责交代：内部子系统、外部依赖、通信关系与边界条件。
- 不要把它画成功能架构图（功能分层）、流程图（时间顺序）、技术架构图（具体技术栈）或部署图（物理拓扑）。

【图元】
- 仅使用五种抽象图元：Actor（绿#E8F5E9）、Node（蓝#E3F2FD）、Store（灰#ECEFF1）、Channel（黄#FFF8E1）、Boundary。
- 复杂系统按需启用扩展色：配置面芽绿#DCEDC8、展现面浅紫#F3E5F5、运营面暖橙#FFF3E0、核心路径深蓝#BBDEFB、业务数据翠绿#C8E6C9。

【布局】
- 根据系统结构本质选择一种主导布局：Layered、Hub-Spoke、Pipeline、Mesh、Star-Plugin、Symmetric。
- 单张图禁止混合多种主导布局。

【连线】
- 实线/虚线含义取决于图类型（逻辑架构图/数据流图/部署图），必须在图一角声明图类型。
- 所有跨边界连线必须标注协议或数据格式，禁止裸线。
- 禁止双向箭头。

【边界】
- 系统边界用粗实线，子域用虚线，外部依赖域与系统边界保持物理间距。
- Actor 不得画在系统边界内。
- Store 之间禁止直连。
- 外部 Actor/Node 不得直接连内部 Store，必须通过受控 Node 或 Channel。

【原则】
- 若用户要求体现低耦合/无侵入/隔离性/灵活性/易用性，必须转译为视觉元素，而不是只在文字说明里提及。

【反模式】
- 禁止：裸线、越层穿透、颜色漂移、Actor入框、存储直连、双向箭头、单图布局混合、无类型声明。
- 也禁止：把系统架构图画成功能清单图、流程图、技术栈图、部署拓扑图。

执行步骤：
1. 确认图类型与布局模式
2. 识别图元与边界（系统边界 vs 外部依赖域）
3. 最小集着色（按需启用扩展色）
4. 布局分组与边界绘制
5. 连线标注（跨边界必须标协议）
6. 反模式校验（逐条检查清单）
7. 工件边界复核：确认它仍然是系统架构图

你的产出必须让架构师和研发人员一眼看清：系统边界、内部子系统、协作关系、外部依赖，以及它与功能架构图的区别。
```

---

## 11. 示例映射（供 Agent 参考，禁止直接复用业务名词）

当用户描述以下场景时，按此抽象映射：

- “用户通过 App 发起请求”
  - 抽象图元：Actor（绿）
  - 备注：位于系统边界外

- “请求先经过 API 网关”
  - 抽象图元：Channel（黄）
  - 备注：位于边界入口，作为系统边界第一层受控接入

- “网关把请求路由到订单服务/支付服务”
  - 抽象图元：Node（蓝）
  - 布局模式：Layered / Hub-Spoke
  - 备注：系统边界内核心 Node

- “订单数据存在 MySQL，日志存在 ES”
  - 抽象图元：Store（灰/绿）
  - 备注：位于 Node 下方；禁止外部直接连 Store；Node 与 Store 关系不要退化成技术细节堆砌

- “支付成功后发消息通知库存系统”
  - 抽象图元：Channel（黄）+ Actor（绿）
  - 连线：虚线箭头
  - 备注：异步事件，虚线

- “我们依赖微信支付的接口”
  - 抽象图元：Actor（绿）
  - 备注：位于外部依赖域；细虚线框包裹；与系统边界保持距离

- “有一个可视化规则配置后台”
  - 抽象图元：Node（芽绿）
  - 布局：通常位于控制/配置面
  - 备注：面积可放大，以体现易用性和管理面重要性

- “通过 Sidecar 做安全校验，业务无感知”
  - 抽象图元：Node（小，芽绿）+ 虚线箭头
  - 备注：挂载在主 Node 侧面；体现无侵入原则

- “多租户之间数据隔离”
  - 抽象图元：Boundary（虚线子域框）
  - 备注：同一 Node 类型重复并列；体现隔离性

- “简单配置用 YAML，复杂逻辑用自定义脚本”
  - 抽象图元：两个并列 Node（芽绿 + 蓝）
  - 备注：配置面与执行面并置；体现灵活性

---

## 12. Lightweight Output Template

When producing or reviewing a 系统架构图, use this framing packet.

- 建设目标：
- 图类型：系统架构图（概要设计）
- 系统边界：
- 外部参与者：
- 内部子系统：
- 关键通道/中枢：
- 关键存储：
- 主协作链：
- 异步/旁路/弱依赖：
- 与功能架构图的边界：
- 已知省略项：

## 13. Review Gates

When reviewing an existing 系统架构图, check these gates.

### Gate 1: System boundary clarity
Can the reviewer tell what is “our system” and what is “external” instantly?

### Gate 2: Internal decomposition clarity
Are the internal nodes true subsystem-level responsibilities rather than a random feature list?

### Gate 3: Dependency direction clarity
Can the reviewer tell who depends on whom, and whether the interaction is sync/async/sidecar/weak?

### Gate 4: Controlled access clarity
Do external actors enter through owned nodes/channels, rather than directly touching internal data or hidden components?

### Gate 5: Artifact purity
Has the diagram stayed a system architecture artifact instead of drifting into function map, process flow, tech stack map, or deployment topology?

### Gate 6: Boundary-condition visibility
Are isolation domains, external dependency zones, sidecars, observers, plugins, or management-vs-execution split shown when they materially affect architecture judgment?

### Runtime-based product overlay rule

When the system being diagrammed is a **product built on top of an existing agent runtime**, the 系统架构图 should not stop at `Management / Gateway / Agent Runtime / External Capability` boxes alone. It should also make the inherited runtime platform capabilities legible at the right abstraction level.

Preferred expression:
- keep the top-level boundary split readable
- inside the `Agent Runtime` region, surface the most architecturally important inherited capability groups, such as:
  - unified agent loop / AIAgent-style control axis
  - host surfaces: CLI / Gateway / Cron / ACP / Batch
  - provider routing / fallback / auxiliary routing
  - prompt assembly + compression / caching
  - tools runtime + MCP + plugins
  - session storage + profile isolation
  - collaboration execution: Single-Agent / Agent Group / Kanban / Cron
  - multimodal I/O when product scope includes image/voice/document interaction

Do not force all implementation detail into one figure, but do not hide these capabilities behind a single vague label like `Agent Runtime` either when the product value proposition depends on them.

### Front-end relationship rule for product systems

If the system has explicit product surfaces (workspace, chat page, admin console, system console), include them as a first-class layer or actor group in the 系统架构图 and make the main request path explicit. Default expression:
- 前端交互页面层 -> Management -> Gateway -> Agent Runtime

This avoids a recurring overview-design failure where the back-end architecture is detailed but the actual product interaction surface is invisible.

## Common Pitfalls

1. Treating 系统架构图 as a lighter version of 技术架构图.
2. Treating 系统架构图 as a more “technical sounding” 功能架构图.
3. Drawing all external systems inside the main box because they matter politically.
4. Keeping only abstract principles while dropping shapes, colors, boundary rules, and line semantics.
5. Letting line semantics vary per page with no declared graph type.
6. Keeping too much deployment detail in an overview design artifact.
7. Using the same visual emphasis for sidecar/observer paths and the main collaboration spine.
8. Forgetting that the drawing grammar itself is part of the deliverable quality.
9. In pipeline-shaped data systems, drawing only the main chain and pushing materially important side stores or replicas into prose. If a side store changes the architecture judgment — for example, “BigQuery hot window” versus “DuckDB full-snapshot lookup replica” — it must appear in the system diagram. Represent the synchronization path through an owned Node/Channel such as a periodic sync/export capability, rather than drawing a bare Store-to-Store direct link.

## Verification Checklist

- [ ] 图的系统边界明确，内外部一眼可分
- [ ] Actor 全部在系统边界外
- [ ] 内部节点是子系统/责任单元级别，不是纯功能清单
- [ ] 外部依赖、通信关系、边界条件都被表达
- [ ] 关键跨边界连线在需要时标注协议/介质/交互类型
- [ ] 异步、旁路、弱依赖、sidecar 关系被区别表达
- [ ] 图没有退化成功能架构图、流程图、技术架构图或部署图
- [ ] 原始绘图规范中的细节层（图元、颜色、形状、文字、线条、布局、边界、反模式、工具适配、示例映射）已被保留
- [ ] 主协作链清晰，但没有关系过载
- [ ] 读者可以据此继续做技术架构、接口设计与部署设计

# Diagram Grammar Reference

This reference preserves the full executable drawing grammar for the `core-functional-flow-diagramming` skill.
Use it when the task requires concrete node/edge semantics, abstraction-level control, typography/color discipline, or a precise pre-drawing checklist.

## 1. 定位与边界（Scope Definition）

### 1.1 本规范回答的问题
为架构师和研发人员绘制**核心功能流程图**，聚焦以下三类问题：
- **业务视角**：有哪些关键业务流程？步骤是什么？
- **系统视角**：流程经过哪些服务/组件？它们如何协作？
- **数据视角**：数据/控制/事件在组件间如何流转？最终落在哪里？

### 1.2 适用范围（明确边界）
✅ **适用**：业务流程流转、服务交互编排、数据管道、事件驱动链路、状态迁移、Saga 补偿流、微服务调用链  
❌ **不适用**：纯静态结构图（如类图、ER 图）、物理部署图（服务器/机房拓扑）、UI 原型图、时序图（强调时间轴和生命周期）  

> **关键原则**：本图关注“**流**”（Flow），而非“**结构**”（Structure）或“**部署**”（Deployment）。

## 2. 抽象元语系统（Abstract Visual Grammar）

### 2.1 节点元语（Node Primitives）
所有节点使用**角色抽象命名**，禁止硬编码技术栈（如 MySQL、Kafka、Redis）。技术选型应在图注或附录中说明。

- **直角矩形**：`[Service]` / `[Component]`
  - 语义说明：独立部署单元或逻辑处理模块
  - 典型场景：微服务、应用、处理引擎
- **圆角矩形**：`[Task]` / `[Job]` / `[Step]`
  - 语义说明：可执行的原子操作或业务步骤
  - 典型场景：ETL 作业、审批步骤、定时任务
- **圆柱体**：`[Store]` / `[Repository]`
  - 语义说明：持久化或半持久化数据载体
  - 典型场景：数据库、对象存储、文件系统、缓存
- **平行四边形**：`[Queue]` / `[Buffer]` / `[Channel]`
  - 语义说明：异步缓冲、消息通道、流式入口
  - 典型场景：消息队列、事件总线、数据管道入口
- **菱形**：`[Decision]` / `[Router]`
  - 语义说明：路由判断、策略选择、网关分流
  - 典型场景：规则引擎、BPMN 网关、Feature Toggle
- **六边形**：`[Gateway]` / `[Adapter]` / `[Facade]`
  - 语义说明：协议转换、入口聚合、防腐层
  - 典型场景：API 网关、文件传输网关、协议适配器
- **云朵**：`[External]` / `[Boundary-System]`
  - 语义说明：组织边界外的第三方或遗留系统
  - 典型场景：外部 API、合作方系统、SaaS
- **人形 / Stick**：`[Actor]` / `[Role]`
  - 语义说明：触发流程的人、部门或外部系统
  - 典型场景：终端用户、运营人员、上游系统
- **圆形**：`[Event]` / `[State]`
  - 语义说明：领域事件、状态快照、信号
  - 典型场景：`OrderCreated`、`PaymentSucceeded`
- **大矩形框（容器）**：`[Domain]` / `[Context]` / `[Tier]`
  - 语义说明：逻辑边界、子系统、分层环境
  - 典型场景：限界上下文、微服务集群、安全域
- **双竖线矩形**：`[Concurrent-Zone]` / `[Fork-Join]`
  - 语义说明：并行处理区域、多实例执行
  - 典型场景：批量并发处理、MapReduce、分支合并

### 2.2 边（连线）元语（Edge Primitives）
边必须带**语义标注**（动词短语或事件名），禁止裸线。

- **实线单箭头** `→`
  - 语义：同步调用、直接请求、命令下发
  - 标注示例：`invoke`, `query`, `execute`, `POST`
  - 适用模式：请求-响应、RPC
- **虚线单箭头** `- - >`
  - 语义：异步投递、事件发布、通知
  - 标注示例：`emit`, `publish`, `notify`, `fire`
  - 适用模式：事件驱动、消息队列
- **实线双向箭头** `<->`
  - 语义：双向通信、同步协商、心跳
  - 标注示例：`negotiate`, `sync`, `poll`, `handshake`
  - 适用模式：长连接、注册发现
- **虚线双向箭头** `<.->`
  - 语义：异步确认、回调、补偿
  - 标注示例：`ack`, `callback`, `compensate`, `retry`
  - 适用模式：Saga、TCC、回调机制
- **粗实线** `═══>`
  - 语义：主路径（Happy Path）强调
  - 适用：关键业务流程
- **点划线** `-.->`
  - 语义：可选路径、降级链路、未来扩展
  - 标注示例：`fallback`, `degrade`, `mock`
  - 适用模式：容灾、灰度
- **折线 / 直角线** `└─>`
  - 语义：跨层调用、绕过中间层
  - 规则：保持流向清晰即可
  - 适用模式：复杂分层架构

### 2.3 并发与分叉表达
- **Fork（分叉）**：一个节点引出多条并行实线，进入 `[Concurrent-Zone]` 双竖线框，框内放置并行任务
- **Join（汇聚）**：多条线汇入菱形或圆形，再引出单线继续下游
- **多实例**：在节点右下角标注 `×N` 或 `[N instances]`，表示该服务/任务存在多个副本处理分片

## 3. 内容抽象与粒度法则（Abstraction Rules）

### 3.1 抽象层级选择（Pick ONE per diagram）
一张图只能处于以下一种抽象层级，禁止混用：

- **L1: 业务流程**
  - 节点含义：业务步骤 / 活动
  - 连线含义：业务规则 / 顺序
  - 适用读者：业务分析师、产品经理
  - 示例节点名：`[Validate-Identity]`, `[Generate-Report]`
- **L2: 服务交互**
  - 节点含义：服务 / 应用 / 容器
  - 连线含义：调用 / 事件 / 数据流
  - 适用读者：架构师、研发负责人
  - 示例节点名：`[Payment-Service]`, `[Notification-App]`
- **L3: 组件协作**
  - 节点含义：模块 / 子系统 / 库
  - 连线含义：接口 / 依赖 / 消息
  - 适用读者：核心研发、技术负责人
  - 示例节点名：`[Pricing-Engine]`, `[Rule-Parser]`

> **原则**：如果一张图同时出现 `[Validate-Identity]`（业务步骤）和 `[Payment-Service]`（服务名），说明混用了 L1 和 L2，必须拆分为两张图或统一层级。

### 3.2 技术栈去耦（Decoupling）
所有节点名必须是**角色名**而非**产品名**：

- `MySQL`, `PostgreSQL` → `[Structured-Store]`, `[Transactional-DB]`
- `Kafka`, `RabbitMQ` → `[Message-Bus]`, `[Event-Channel]`
- `Redis`, `Memcached` → `[Cache-Layer]`, `[Hot-Store]`
- `HDFS`, `S3`, `MinIO` → `[Object-Store]`, `[Distributed-Storage]`
- `Spark`, `Flink` → `[Batch-Engine]`, `[Stream-Processor]`
- `Elasticsearch` → `[Search-Index]`, `[Fulltext-Store]`

### 3.3 核心检查清单（Pre-Drawing Checklist）
在绘制前，必须确认：
1. **触发源**：谁 / 什么启动流程？（Actor / Timer / Event / Upstream）
2. **编排模式**：是顺序执行、并行分叉、事件广播还是 Saga 补偿？
3. **关键节点**：3-9 个核心节点（超过 9 个考虑拆图或抽象子域）
4. **数据终点**：最终状态落在哪里？（Store / External / Actor Notification）
5. **闭环 / 开环**：流程是单向结束，还是有反馈 / 循环？
6. **异常路径**：主失败场景是什么？是否有补偿或降级？

## 4. 颜色与视觉策略（Visual Strategy）

### 4.1 基础调色板（Base Palette）
采用**语义着色**而非“美观着色”，一张图中彩色不超过 3 种。

- **中性结构**：深灰 `#333333`
  - 用途：所有节点边框、常规文字、默认路径
- **背景 / 边界**：浅灰 `#F5F5F5`
  - 用途：容器背景、环境边界填充（透明度 10-20%）
- **主路径强调**：深绿 `#2E7D32`
  - 用途：仅对 Happy Path 的边框或连线使用，突出核心流程
- **异常 / 补偿**：暗红 `#C62828`
  - 用途：仅对失败路径、补偿流、告警使用（可选）
- **外部 / 跨越边界**：深蓝 `#1565C0`
  - 用途：仅对跨越组织边界的外部系统连线使用（可选）
- **事件 / 异步**：橙色 `#EF6C00`
  - 用途：仅对事件总线、消息、信号使用（可选，与深蓝二选一）

### 4.2 着色粒度规则
- **方案 1（推荐）**：仅对**连线**着色（主路径绿、异常红、外部蓝），节点保持深灰——最清晰
- **方案 2**：仅对**关键节点边框**着色，内部填充保持白色/透明
- **方案 3**：对**容器背景**着色以区分安全域/环境，内部节点保持中性
- **禁止**：渐变填充、阴影、3D 效果、超过 3 种彩色同时出现

### 4.3 字体与排版
- **字体族**：无衬线（Inter, Helvetica, Arial, 思源黑体）
- **字号层级**：
  - 容器 / 域标题：14-16px，加粗
  - 节点名称：12-13px，常规
  - 连线标注：11-12px，斜体或常规
  - 阶段编号 / 图例：10-11px
- **文字方向**：一律水平；空间不足时优先**扩大画布**或**简化文字**，绝不旋转 90°
- **命名规范**：
  - 节点：`[PascalCase]` 或 `[kebab-case]`，如 `[Order-Service]`, `[Risk-Engine]`
  - 连线：小写动词或事件名，如 `submit`, `order.created`, `compensate.inventory`

## 5. 详细设计语境下的增强规则

在保留完整绘图细节的同时，必须额外满足 detailed-design key-flow doctrine。

### 5.1 角色 / 阶段显式化
如果不同角色、不同阶段会改变处理逻辑，则必须在图中体现，方式可以是：
- swimlane
- staged grouping
- role containers
- explicit ownership markers

### 5.2 分支完整性
分支必须至少覆盖到设计评审真正关心的层级：
- normal path
- exception path
- rollback / compensation path when relevant
- pending ownership when relevant
- terminal success / controlled failure / cancellation

### 5.3 状态闭环
如果关键复杂度来自状态推进，则必须：
- 改用状态机
- 或补一张状态机
- 或在 flow 旁边加 terminal-state / transition summary

### 5.4 不重不漏的真正含义
“不重不漏”不是把所有代码分支都画出来，而是：
- 在当前抽象层级下
- 所有**评审相关**的关键分支都被覆盖
- 没有把同一语义重复拆成多个视觉噪音节点

## 6. Minimal Output Contract

When producing this artifact for a design doc, aim to deliver:
- the diagram itself
- a one-paragraph scope statement: what key flow this diagram answers
- a short note on why flowchart vs state machine was chosen
- if needed, a branch inventory or terminal-state summary beside the figure

For non-trivial flows, also include:
- known out-of-scope branches
- companion artifacts when split was necessary

## 7. Anti-Patterns

❌ **混用抽象层级**：同一张图里既有 `[Validate-Order]`（业务步骤）又有 `[Spring-Boot-App]`（技术实现）。  
❌ **画成时序图**：出现 Lifeline（生命线）、Activation Bar（激活条）、按时间生灭的对象——那是另一种图。  
❌ **无标注裸线**：任何连线必须有语义（动词或事件名），否则读者无法区分是调用、数据流还是通知。  
❌ **技术名词硬编码**：图中出现具体版本号、IP、端口、中间件品牌——应移至图注。  
❌ **过度细节**：把函数级别的调用链画在架构流程图中；函数级调用应使用序列图或代码文档。  
❌ **环形缠绕**：主路径画成闭合圆环；应展开为线性流或用右侧返回箭头表达循环。  
❌ **滥用菱形**：把每个 if/else 都画成菱形判断；核心功能流程图关注“流经哪些组件”，判断逻辑内化于服务或标注即可。  
❌ **静态结构冒充流程**：只画了一堆框和线，但没有明确的起点和终点，也没有流向——那是系统结构图，不是流程图。  
❌ **Happy-path-only diagrams**：没有异常、补偿、取消、pending ownership。  
❌ **Missing terminal ownership**：流程停在“待处理”却不说明谁接手。  
❌ **Decision clutter**：每个局部实现分支都被画出，淹没评审真正关心的节点。  
❌ **Flowchart pretending to be technical architecture**：中间件、部署载体、进程拓扑压过了关键流程本身。  


# Layout Cookbook Reference

This reference preserves the concrete layout patterns for core functional flow diagrams.
Choose one dominant pattern per figure unless a split view is clearly justified.

## 布局模式库（Layout Patterns）

**禁止强制单一布局**。根据架构范式选择以下模板之一：

### 1. 模式 A：横向流（Linear Flow）
```text
[Actor] → [Gateway] → [Service] → [Service] → [Store] → [External]
```
- **适用**：数据管道、ETL、简单请求链、审批流
- **规则**：主方向左→右；若需回调，从右侧用虚线返回左侧，避免环形缠绕
- **常见误用**：把过多补偿支路和并行支路挤进同一条主干，导致左右回线打结

### 2. 模式 B：分层架构（Layered Stack）
```text
┌─────────────────────────────────────┐
│  [Presentation] / [API-Gateway]     │  ← 接入层
├─────────────────────────────────────┤
│  [Orchestration] / [Process-Engine] │  ← 编排层
├─────────────────────────────────────┤
│  [Core-Domain] / [Business-Services]│  ← 领域层
├─────────────────────────────────────┤
│  [Store] / [Repository]             │  ← 持久层
└─────────────────────────────────────┘
```
- **适用**：单体分层、微服务分层、Clean Architecture 流程展示
- **规则**：层内组件水平排列，层间调用垂直向下；允许跨层虚线（如缓存直读）
- **常见误用**：把部署节点、机器、K8s 对象混进层级容器

### 3. 模式 C：中心辐射（Hub-and-Spoke）
```text
         [External-A]
             ↑
[Actor] → [Core-Platform] → [Store]
             ↓
         [External-B]
```
- **适用**：中台架构、集成平台、网关聚合、支付路由中心
- **规则**：中心节点放大或使用强调色；辐射线标注不同协议/事件类型
- **常见误用**：所有外部点都直连中心但没有语义标注，导致图退化成“星型结构图”而非流程图

### 4. 模式 D：事件总线（Event Mesh）
```text
[Service-A] ──emit──> ○ [Event-Bus] ○ <──emit── [Service-B]
                │                         │
                └──consume──> [Service-C]   └──consume──> [Service-D]
```
- **适用**：Event-Driven Architecture、CQRS、最终一致性系统
- **规则**：事件总线用圆形或平行四边形居中；发布者用虚线箭头指向总线；消费者从总线引出虚线箭头
- **常见误用**：总线只是画成一个盒子，但没有标出 emit / consume 关系

### 5. 模式 E：闭环控制（Closed Loop）
```text
[Sensor/Actor] → [Controller] → [Actuator/Service]
                      ↑____________│
                           (feedback)
```
- **适用**：调度系统、监控系统、自适应流控、AI Agent 决策循环
- **规则**：反馈线用虚线或点划线，标注 `feedback`、`metric`、`state-sync`
- **常见误用**：把循环画成完全对称闭环，丢失主控制方向

### 6. 模式 F：Saga / 补偿流（Compensation Flow）
```text
[Step-1] → [Step-2] → [Step-3] → [Step-4]
   │          │          │          │
   └─compensate←──compensate←──compensate←──┘
```
- **适用**：分布式事务、长事务、可回滚业务流程
- **规则**：主路径实线向右；补偿路径虚线向左下方回退，标注 `compensate`、`undo`、`rollback`
- **常见误用**：补偿路径与正常重试路径混为一谈

## 补充选型规则

### 何时优先 flowchart
- 角色切换明显
- 阶段推进明显
- 审批、路由、编排、人工介入较多
- 关键分支是“谁接手、下一步做什么”

### 何时优先 state machine
- 状态推进是主要复杂度
- 正确性依赖合法状态迁移
- rollback / cancel / retry / pending 的状态边界很重要

### 何时拆成多图
- 主路径和补偿路径已经互相遮挡
- 流程与状态复杂度同等重要
- 一图无法在可读性前提下做到不重不漏

推荐拆法：
- main flow + exception supplement
- main flow + state machine supplement
- actor-facing flow + internal handling supplement


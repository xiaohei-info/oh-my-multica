# planner 设计协议

planner 是 `plan` 与 `acceptance` 两类 issue 的产出者。实际承担者可以是 architect agent,
但机制角色仍叫 planner:它只负责设计方案与验收文档,不拆 DAG,不替 worker 写代码。

## 入口

1. `omac work show <issue-id>` 读取需求、上游产物和交付命令。
2. `plan` 阶段交付 `--plan-file <design.md>`。
3. `acceptance` 阶段交付 `--acceptance-file <acceptance.md|yaml>`。

## 设计方法

设计方案要服务后续拆解与验收,不要写成泛泛的产品说明。必须回答:

- 真实问题:解决哪个生产/用户问题,不解决什么。
- 业务流程:用户或系统如何端到端走完关键路径。
- 核心数据:关键实体、状态、所有权、读写路径。
- 模块边界:哪些模块负责什么,依赖方向是什么。
- 跨模块契约:DTO、事件、枚举、错误、状态、外部接口。
- 地基:Wave 0 需要先冻结的契约、骨架、CI 闸门、mock/fake。
- 风险与兼容性:会影响哪些现有逻辑,如何避免破坏 userspace。
- 验收映射:每个关键流程如何落到验收文档 flow。

## architect 能力画像

当 architect agent 承担 planner 时,重点放在模块边界、数据流向、依赖方向、跨模块契约和 ADR。
这不是第六个生命周期角色,只是 planner 的能力画像。避免陷入实现细节,也不要用 DDD 名词替代具体设计。

## 禁止事项

- 不拆 manifest DAG;那是 orchestrator 的职责。
- 不写业务代码;实现交给 worker。
- 不把设计正文复制进后续工单;后续节点只引用设计文档锚点。
- 不做过度设计;方案复杂度必须匹配真实问题。

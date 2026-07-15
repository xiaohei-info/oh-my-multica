# Source Basis

This architect-private skill is derived from two local sources:

1. User-provided draft: `技术架构图Skill.md`
2. Obsidian inbox note: `Inbox/能力全景图/专业技术能力/软件架构设计的生命周期.md`

Lifecycle basis extracted from the second source:
- 技术架构图 belongs to **详细设计 / 技术架构**
- It must answer:
  - 系统设计中的功能通过什么技术栈实现？如何选型的？
  - 项目结构是什么样的？
  - 系统运行起来后，服务、进程、任务、调用链路如何协作？
  - 稳定性如何保障？
- It sits adjacent to, but must stay distinct from:
  - 项目架构图
  - 关键流程图 / 状态机
  - 数据架构 / ER 图
  - 接口定义文档
  - 非功能性需求设计
  - 部署 / 运维 / 监控 / 容灾文档

Preservation rule:
- The original user-provided draft is preserved **verbatim** in:
  `references/verbatim-user-draft-技术架构图Skill.md`
- The normalized full-detail working reference is preserved in:
  `references/universal-technical-architecture-diagramming-spec-full.md`
- The umbrella SKILL.md adds architect lifecycle placement, technical-design review gates, and execution workflow on top of those sources.
- Authority order for preservation disputes:
  1. `references/verbatim-user-draft-技术架构图Skill.md` (highest authority for exact wording/structure preservation)
  2. `references/universal-technical-architecture-diagramming-spec-full.md` (working full-detail reference for practical loading)
  3. `SKILL.md` (architect usage layer)
- If a future summary conflicts with the verbatim draft on drawing detail, exact wording, structure, or example preservation, the verbatim draft wins.


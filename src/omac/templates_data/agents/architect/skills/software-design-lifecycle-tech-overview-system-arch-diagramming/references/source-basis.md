# Source Basis

This architect-private skill is synthesized from two inputs:

1. `软件架构设计生命周期` in the Obsidian vault:
   - technical-design stage includes 功能架构、系统架构、技术架构、关键流程、数据架构、外部接口与非功能性需求
   - diagram responsibility split:
     - 功能架构图 = 把设计层、运行层、数据底座与运营支撑拆开
     - 系统架构图 = 交代内部子系统、外部依赖、通信关系与边界条件
     - 技术架构图 = 压到具体技术栈、引擎、中间件、数据存储与执行路径

2. User-provided generic system architecture diagram spec draft:
   - preserve the detailed drawing grammar, not only the high-level intent
   - five primitive vocabulary: Actor / Node / Store / Channel / Boundary
   - boundary-first modeling
   - layout selection by structural essence
   - shape / text / line semantics
   - cross-boundary relation labeling
   - anti-pattern checks such as Actor 入框、存储直连、双向箭头、布局混合、无类型声明
   - tool adaptation guidance
   - example mappings

## Explicit retention rule

The user explicitly required that published drawing details must remain intact and must not be compressed away.
Therefore this skill keeps:
- primitive vocabulary
- color semantics
- shape rules
- text rules
- line rules
- layout modes
- boundary taxonomy
- anti-pattern list
- SOP flow
- tool-adaptation guidance
- example mappings

## Why this skill is separate from 功能架构图 skill

The architect profile already has a neighboring local skill:
- `system-functional-architecture-diagramming`

That skill answers:
- what the system should do
- what functional blocks exist
- how capabilities are layered

This skill answers:
- what subsystems exist inside the system
- what stays outside
- how internal and external parts communicate
- what the boundary and dependency conditions are

## Intended role in the overview-design packet

Recommended neighboring artifact split:
- 功能架构图: capability and layering view
- 系统架构图: subsystem / dependency / communication / boundary view
- 技术架构图: concrete technology realization view
- 流程/时序图: dynamic execution view
- 部署图: physical topology view


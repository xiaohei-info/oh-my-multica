---
name: software-design-lifecycle-tech-overview-methodology
description: "Use when an architect must run the overview-design portion of technical design, preserve the full lifecycle-stage doctrine for shared technical-design entry and overview design, and define goals, alternatives, functional layering, system structure, risk, and overview-level design boundaries before detailed design begins."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, technical-design, overview-design, architecture]
    related_skills: [arch-lifecycle-delivery, arch-lifecycle-tech-overview-functional-arch-diagramming, arch-lifecycle-tech-overview-system-arch-diagramming, arch-lifecycle-tech-detailed-methodology]
---

# Technical Overview Methodology

## Overview

This is the `architect` profile's **技术方案设计 → 概要设计 方法论 skill**.

## When to Use

Use when:
- the solution-design package is already confirmed
- the architect must define overview-level technical structure before adapter-level or field-level detail
- the task is to produce or review 概要设计 / 总体设计 / high-level technical design

## Canonical Stage Doctrine (Full Preservation)

# 三、技术方案设计

**时间**：业务解决方案设计文档与相关方review确认后

**输入**：需求调研文档+业务解决方案设计文档+业务方相关反馈

**输出**：技术方案设计文档，包含概要设计与详细设计

**过程**：

在最初的系统方案或者概要设计中提供 
- 备选方案设计：提供1~2个备选方案，需要和主方案有明显差异，弥补主方案可能存在的缺陷，提供其他角度来审视解决方案，可以在备选方案中积极引入新技术，备选方案不展开细节，确定方案后再进一步细化 
- 备选方案评估：各个维度综合评比（性能、可靠性、复杂度、可运维性、硬件成本、开发周期），给出优先级和权重，选择最合适的方案

| 阶段   | 步骤     | 问题                                                                                                                                                        | 答案                                                                                                                                                                                        |
| ---- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |

| 概要设计 | 需求背景   | 背景、现状、问题、分析总结                                                                                                                                             |                                                                                                                                                                                           |
|      | 需求分析   | 要解决什么问题？解决方式是什么？                                                                                                                                          |                                                                                                                                                                                           |
|      |        | 有哪些术语定义？                                                                                                                                                  |                                                                                                                                                                                           |
|      | 设计目标   | 系统建设目标是什么样的、要有哪些功能？                                                                                                                                       |                                                                                                                                                                                           |
|      |        | 这些功能是如何分层的？                                                                                                                                               | 给出功能架构图，并保留可编辑源文件和必要的导出产物。                                                                                                                                                                       |
|      | 架构设计   | 有哪些设计原则？                                                                                                                                                  | 例如： - 低耦合：按照功能拆分为物理上不同的模块，减少各模块之间的耦合，提升开发效率。 - 无侵入：与常规业务代码校验相比，资金安全平台目标是对业务代码无影响，业务系统无感知。 - 隔离性：可实现多系统，系统间不同规则互不影响。 - 灵活性：简单校验写sql，复杂校验可以自定义函数满足各类业务场景校验需求。 - 易用性：可视化平台，方便用户在线添加、修改个性化需求。 |
|      |        | 有哪些复杂度需要分析？ <br><br>_架构设计的本质目的是为了解决软件系统的复杂性，在设计架构时首先就要分析系统的复杂性_                                                                                           | 例如：高性能、可靠性、扩展性、安全性                                                                                                                                                                        |
|      |        | 整体系统架构是什么样的？模块如何协作、边界如何划分？ <br><br>_子系统划分与联动、系统内部的实现方式、与外部系统的依赖关系与交互_ _、各个模块功能、职责与分层明细_ _把协作方的系统也考虑进来，当做不可靠的外部依赖设计_                                       | 给出系统架构图，并明确模块职责、边界、依赖关系和关键交互。                                                                                                                                                                    |
|      | 风险与约束  | 当前系统需要重点解决哪些技术风险与工程约束？例如并发、幂等、超时重试、数据一致性、安全隔离、容量上限、可运维性等                                                                                                  | 风险约束清单                                                                                                                                                                                    |

## Architect Execution Layer

- keep the abstraction level at system / subsystem / capability / contract level
- separate 功能架构 and 系统架构 instead of collapsing them
- when an overview doc has been revised across many rounds, proactively refactor it back into a stable overview package (for example: 文档定位 → 输入/采用判断 → 目标与非目标 → 推荐架构 → 功能视角 → 系统视角 → 共享契约 → 风险约束 → 验证), instead of preserving the historical discussion order
- prefer system-level capability planes, boundary statements, and shared contracts over long step-by-step narration; keep only a few canonical主链 to illustrate the architecture, not to become the document spine
- surface alternative plans and evaluation criteria before presenting one path as inevitable
- use risk and constraints to shape structure early
- **verify external capability boundaries BEFORE proposing subsystem划分**: read actual API endpoints, source code, tool registry, or provider documentation instead of assuming subsystem names that may not exist in reality
- **inherit business solution document's layering structure**: when a solution-design doc already defines a clear layered structure (such as 前端展示层 → 业务服务层 → 适配层 → 外部基座层), adopt those layers directly; do not invent new technical-layer names that diverge from the business solution's architecture diagram
- **for control-plane-heavy product systems, unify same-domain business control capabilities under one Management subsystem first**: do not prematurely split employees/templates/permissions/governance/task-entry into many peer一级子系统 when they are all part of one business control-plane domain; keep them as internal modules of Management unless independent ownership or lifecycle already matters now
- **when the reused runtime already owns collaboration execution, define the self-built task side as translation and packaging only**: in overview docs, describe the self-built layer as business-task translation, runtime request generation, event mapping, and result packaging; do not imply a custom orchestration/runtime subsystem if delegate/kanban/cron-style execution is actually provided by the reused Agent Runtime
- **include front-end page groups explicitly when the product is workspace-driven**: overview design should show enterprise frontstage, collaboration/chat pages, enterprise back office, and system back office as part of the architecture package, and make the main relationship explicit as 前端页面 -> Management -> Gateway -> Runtime rather than treating the front end as an omitted shell
- **for products built on top of an existing agent runtime, surface inherited platform capabilities explicitly**: if the reused runtime already provides a unified agent loop, multiple host surfaces (CLI / Gateway / Cron / ACP / Batch), provider routing and fallback, prompt assembly, compression/caching, tool runtime, skills, memory, plugins, MCP, session storage, profile isolation, or multimodal I/O, call these out in the overview package as inherited platform capabilities. Otherwise reviewers will underestimate the product's real capability ceiling and may design redundant subsystems.
- **prefer abstract external-facing terms over concrete product names in architecture artifacts**: when the document is for solution review rather than implementation debugging, use stable terms such as Agent Gateway and Agent Runtime in正文/图节点, and keep concrete vendor/product names only as implementation notes or reuse explanations
- when a control-plane split only isolates future change but does not unlock present-day capability, do **not** freeze that split as a current design fact too early; prefer a V1 合一对象模型 + 明确的 future-split boundary in the overview doc, and only promote registry/spec、contract/manifest、catalog/binding-style separations into first-class objects when version governance, independent lifecycle, or multi-team ownership already matters now
- when the user asks to produce a **new independent v2 overview document**, do not revise the old overview doc in place
  - create a separately named overview markdown plus versioned diagram assets
  - keep the previous version intact for comparison and rollback
- when splitting business-solution vs technical-overview artifacts, actively move concrete technology names, reuse judgments, runtime shapes, gateway implementation choices, and external-base details **out of** the business solution doc and **into** the overview doc
  - the overview doc should become the single authoritative place for those technical adoption and boundary statements
- when producing versioned overview diagrams, keep the markdown's embedded SVG references pointed at the matching versioned companion assets (for example `*-v2.svg`), and verify the paired HTML wrappers still inline the same SVG source
- in formal overview-design docs, remove delivery-report language and historical-cleanup narration that belong to work logs rather than design artifacts. Avoid sections or wording such as `交付清单`, `本次术语收敛`, `不再使用以下旧术语`, or other change-log style phrasing unless the user explicitly asks for migration notes
- after business-solution design has been finalized, overview design should explicitly prove requirement coverage against the business solution / BRD / PRD rather than assuming it is obvious. Add a concise `需求承接/覆盖关系` explanation that maps business-facing demands (for example frontstage/backstage/system-backstage, talent market, private chat, group collaboration, knowledge base, memory, loop scheduling, cost governance) onto the chosen technical structure
- if an upstream solution-design doc previously described a placeholder note as empty, and that note later becomes the real overview-design document, update the upstream reference text in the same round so the document set stays self-consistent

## Gateway-vs-control-plane boundary for runtime-based products

When the product is built on top of an existing agent runtime that already exposes an API server / gateway surface, do **not** assume the gateway and the business control plane are the same thing just because both speak HTTP.

Required verification path:
1. inspect the runtime gateway source and route registrations
2. classify which endpoints are **runtime-native** (for example chat/responses/runs/events/approval/jobs)
3. check whether the gateway actually has first-class business objects such as employees, templates, spaces, permissions, governance, or business task definitions
4. inspect at least one official or recommended companion UI to see whether it:
   - talks to the gateway over HTTP, or
   - imports runtime modules directly / embeds the runtime in-process
5. only after that decide whether the product's own control plane should sit:
   - upstream of the gateway as a business/BFF layer, or
   - directly on the runtime API surface

Default judgment for AI-Team-like products:
- if the gateway exposes runtime-native APIs (chat/responses/runs/jobs) but **does not** expose business entities such as employee/template/permission/workspace objects, keep **Team Panel** as the business control plane **upstream** of **Agent Gateway**
- do **not** move business-facing management interfaces into Gateway just because Gateway already has HTTP endpoints
- do **not** require Team Panel to wrap every Gateway endpoint 1:1; prefer selective translation/projection:
  - Team Panel owns business objects, bindings, governance, task semantics, and product-facing result views
  - Agent Gateway owns runtime submission, event streaming, approval, stop, and scheduler/runtime-native surfaces
- if a system needs an expert/debug console later, that console may talk to Gateway more directly without redefining the main product path

Design consequence:
- in the **product main chain**, use `Frontend -> Team Panel -> Agent Gateway -> Agent Runtime`
- in the **deployment / ownership view**, Team Panel and Agent Gateway can still be separate peer subsystems with different responsibilities
- rename any Gateway box that really contains business management semantics; those interfaces belong in Team Panel, not in the runtime access layer

Reference: `references/panel-vs-gateway-boundary-from-source.md`

## Common Pitfalls

### Pitfall: Overview design drifts into a change log or process walkthrough

**表现**: After multiple review rounds, the document keeps accreting sections and local explanations until it reads like a逐过程说明书 or discussion transcript rather than a stable system overview.

**后果**:
- reviewers cannot quickly identify the real architecture skeleton
- functional/system views become mixed with scenario narration
- inherited platform capabilities get described as scattered details instead of one coherent capability surface
- detailed design starts from unstable prose instead of stable boundaries

**正确做法**:
1. stop editing in-place as if the current order were sacred
2. regroup the document back into a stable overview spine: 定位, inputs/adopt judgment, goals/non-goals, recommended architecture, functional view, system view, shared contracts, risks, verification
3. compress repetitive scenario/process content into only a few canonical主链
4. describe reused runtime abilities as capability domains / shared contracts first, not as a long incremental module dump
5. make diagrams and prose match this abstraction level

### Pitfall: Assuming subsystem划分 that doesn't match external capability reality

**表现**: Proposing subsystem names like "management / gateway / agentos" based on conceptual reasoning without verifying that the external base actually has those separations.

**后果**: Overview design drifts from reality, creating subsystem boundaries that cannot be implemented because the external system doesn't expose them.

**正确做法**: 
1. Identify which external systems will be reused, such as an agent runtime, retrieval service, skill catalog, or AI relay.
2. Read their actual structure: API endpoints, source code routes, tool registry, config schema
3. Map real capabilities to AI Team's adaptation layer before defining internal subsystems
4. Only propose subsystem划分 that can be backed by verified external boundaries

**Example**: A reused agent runtime may expose job-management operations through its gateway API instead of a separate management subsystem. The subsystem design must follow the verified external boundary, such as Gateway plus Agent Runtime, rather than inventing unsupported layers.

## V1 complexity control for overview design

When reviewing or drafting 概要设计, explicitly test each proposed object split with this question:
- "Does this split add a capability we need now, or only make future change cleaner?"

If the answer is mostly "future change cleaner", the default overview-design move is:
1. collapse the pair into one V1 definition object at the prose level
2. describe which semantics are still distinct inside that object (for example `current_version`, `active_binding`, versioned definitions)
3. state the future split trigger explicitly, such as independent lifecycle, independent release cadence, rollback pressure, or separate team ownership
4. keep the detailed split as an evolution path, not as mandatory present architecture

Typical examples:
- `Family Registry` + `Family Spec` -> V1 `Family Definition`
- `Public Contract` + `Read Manifest` -> V1 `Read API Definition`

This avoids a common overview-design failure: treating governance refinement as if it were a required architectural boundary before the system has earned that complexity.

## Companion Diagram / Artifact Skills

- `arch-lifecycle-tech-overview-functional-arch-diagramming`
- `arch-lifecycle-tech-overview-system-arch-diagramming`

## Verification Checklist

- [ ] Shared technical-design entry conditions are satisfied
- [ ] Alternative plans and evaluation logic are explicit
- [ ] Overview design stays at the right abstraction level
- [ ] Functional vs system architecture artifact boundaries are clear
- [ ] External capability boundaries verified (API endpoints, source code, tool registry read before subsystem划分 proposed)
- [ ] Subsystem names inherit from business solution document's layering structure where applicable
- [ ] Detailed design is not started on an unstable overview base

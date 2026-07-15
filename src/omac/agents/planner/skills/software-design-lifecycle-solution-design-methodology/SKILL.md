---
name: software-design-lifecycle-solution-design-methodology
description: "Use when an architect must run the solution-design stage after demand research, preserve the full lifecycle-stage doctrine for this stage, and turn business research into solution-design outputs such as business flow, domain model, solution architecture, risk controls, and milestone framing."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, solution-design, business-architecture, risk]
    related_skills: [arch-lifecycle-delivery, arch-lifecycle-solution-design-biz-flow-diagramming, arch-lifecycle-solution-design-domain-modeling, arch-lifecycle-solution-design-biz-arch-diagramming, ddd-domain-modeling-for-architecture]
---

# Solution Design Methodology

## Overview

This is the `architect` profile's **解决方案设计阶段方法论 skill**.

It owns the whole stage, not only one diagram.

## When to Use

Use when:
- 需求调研文档与相关反馈已确认
- the architect must define the中层方案 rather than jump straight into technical implementation
- the team needs to answer business flow, domain model, business-solution architecture, dependency reliability, state closure, risk, and milestone questions in one stage package

## Canonical Stage Doctrine (Full Preservation)

# 二、解决方案设计

**时间**：业务需求调研文档与相关方review确认后

**输入**：业务需求调研文档+业务方的相关反馈

**输出**：解决方案设计文档，包括业务流程、解决方案架构图等

**过程**：

| 阶段                       | 类型   | 问题                                                                              | 答案                                                                                |
| ------------------------ | ---- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| - 背景 需求背景总结，相关人、相关文档引用   | what | 汇总梳理需求背景                                                                        |                                                                                   |
| - 现状 问题与分析，总结其需求背景中存在的问题 | why  | 系统现状是怎么样？                                                                       |                                                                                   |
|                          |      | 为什么要做这件事情？不做这件事情有哪些影响？                                                          |                                                                                   |
| - 目标 总结其需求评估中期望为业务方带来的效果 |      | 项目的产出是什么？衡量指标是什么？                                                               |                                                                                   |
| 方案                       | how  | 业内有哪些相似的主流方案？ *充分参考、借鉴行业内的成熟经验，特别是成熟的开源项目重点关注，判断是否可以基于已有的开源软件进行二次开发以快速实现方案落地。 * |                                                                                   |
|                          |      | 业务流程有哪些？ _业务问题与需求拆解，不同的角色与对应的需求问题_                                              | 给出业务流程图                                                                           |
|                          |      | 领域模型如何设计？                                                                       | 给出领域模型图（DDD领域驱动设计模型）                                                              |
|                          |      | 业务解决方案的架构是什么样的？ _自顶向下 or 业务向内容，用户/业务操作视角，阐述业务操作流程与问题的解决方案_                      | 给出业务解决方案架构图，示例如下：![](../../../../repository/images/软件架构设计的生命周期-1779158298783.png) |
|                          |      | 方案依赖哪些外部系统、上下游系统或第三方能力？这些依赖是否可靠，异常时如何降级、兜底或隔离？                                  |                                                                                   |
|                          |      | 核心业务状态是否形成闭环？正常流程、异常流程、回退流程分别是什么？                                               | 给出状态流转图 / 异常分支说明                                                                  |
|                          |      | 当前方案可能存在哪些风险点？分别来自业务规则、外部依赖、权限安全、单点故障、审计缺失还是容量瓶颈？对应治理策略是什么？                     | 风险清单 + 治理策略                                                                       |
|                          |      | 实施节奏是什么样的？有哪些里程碑？ _概要设计、详细设计、开发周期、上线时间点等大周期划分与迭代规划_                             | 给出实施计划甘特图并标明对应的负责人                                                                |

## Architect Execution Layer

- run mature-solution survey before locking into custom structure
- keep business flow, domain model, business-solution architecture, and state/risk views distinct
- do not postpone dependency unreliability, fallback, isolation, governance, or milestone questions
- split artifacts when one picture starts absorbing too many responsibilities
- when the deliverable is a正式业务解决方案文档 and the structure clearly benefits from a top-level visual, include the business-solution architecture diagram in the same delivery round rather than leaving the document as pure prose and waiting for a follow-up request

## Special Pattern: Collaboration / Agent-Cluster Solution Design

- when the business ask is a multi-agent collaboration capability, agent cluster, orchestration runtime, or chat-surface coordination system rather than a single isolated feature, add the following solution-design checks before freezing the business solution:

- separate the **user-visible surface** from the **runtime truth layer**
  - for example: chat/channel/thread is the visible workspace; task tree + shared state is the real collaboration object
- if **any participant can be the entry point**, still require a **single root owner** for each root task and a **single final delivery exit**
- separate **formal external collaboration nodes** from **node-local internal parallelism**
  - internal delegate/parallel execution should not automatically pollute the external responsibility graph
- if the user already has a strong mechanism draft, do **not** restart discovery from zero
  - instead, lift it upward into a business solution document that makes explicit: background, goals, scope/non-goals, roles, closed loop, risks, and reuse judgment
- when the deliverable is a **formal business-solution design document** rather than a discussion note, write in the **current settled architecture voice**
  - remove process-language such as “本轮讨论 / 上一版 / 旧方案 / 调整为 / 这版” from the main body unless the user explicitly asks for decision-history retention
  - prefer one coherent present-tense solution narrative over a before-vs-after retelling
- when source materials mix **productized/codename naming** with **technical/responsibility naming**, explicitly define a **terminology convergence boundary** before editing the formal solution artifacts
  - formal solution documents, architecture review outputs, and business-solution / technical architecture diagrams should converge to **technical responsibility-oriented terms** rather than keeping product nicknames as internal module names
  - PRD prototypes, demo copy, and market-facing narrative may retain the **product-facing term** when that term is part of the UX or product story
  - do not stop at正文 replacement only: if the formal artifact set includes diagrams, legends, or architecture examples, converge those assets in the **same pass**
  - do not over-normalize by rewriting product/market artifacts unless the user explicitly asks for full-library unification across both technical and product layers
  - see `references/terminology-convergence-layering.md` for a concrete layered example
- when the business object itself is a **Harness-style control architecture**, describe Harness as the **solution’s primary control architecture**, not as a side runtime hidden inside a larger generic mechanism
  - if Guide/Sensor × Computational/Inferential is the real control spine, use that 2×2 explicitly as the main explanatory axis in the solution section instead of treating Harness as a minor submodule
- for MVP, prefer **one surface + one lightweight truth store** before multi-channel expansion or heavy workflow-kernel design
- when the solution direction has already pivoted and the user asks for an updated business-solution document, normalize the final artifact to the **latest chosen architecture only**
  - do not keep “old方案 vs 新方案” comparison prose, transitional caveats, or stale examples in the main document unless the user explicitly asks for decision history
- if the design pivot changes the architecture picture, update the examples and the business-solution architecture diagram in the same pass
  - do not leave placeholders such as “旧图已失效，之后再补” in the final document delivered for review
- when the user wants a **business-facing v2 solution document**, explicitly strip out底层技术实现细节 from the solution artifact
  - do **not** foreground concrete runtime names, open-source product choices, protocol names, adapter details, or implementation mechanisms in the business solution doc unless the audience explicitly asks for them
  - instead, explain the solution through business modules, user roles, operating entry points, closed-loop flows, and governance outcomes
  - treat concrete technology choices as material for the technical overview / 概要设计 layer, not the business solution layer
- when asked to produce a **new independent v2** instead of revising in place, create a separately named document and matching versioned diagram assets rather than mutating the existing canonical files
  - keep the old version readable
  - keep the v2 business doc's embedded assets self-consistent with its own versioned `resources/` files

Additional pitfall from live design iteration:
- do **not** overvalue persistent chat-surface session continuity just because it makes collaboration feel natural
- ask explicitly whether the business actually needs the same role/profile to be **reused multiple times across one task tree**
- if yes, check whether a long-lived group/chat seat would create role/context aliasing (the same role acting once as an upstream owner and again later as a fresh child worker inside one continuous conversation)
- when that aliasing risk exists, prefer a **runtime-first / fresh-worker** model: role and worker invocation are separate concepts, task continuity comes from harness + task tree + sealed/shared state, and the chat surface is demoted to an adapter rather than treated as the canonical runtime
- once the solution chooses the fresh-worker model, avoid inventing extra “internal node” business abstractions unless they represent real asynchronously delegated work; formal async delegations should usually collapse into one unified task-tree node model

This pattern is especially important for Telegram/Slack/Discord-style agent collaboration proposals where the visible conversation is tempting to mistake for the runtime itself.

## Companion Diagram / Artifact Skills

- `arch-lifecycle-solution-design-biz-flow-diagramming`
- `arch-lifecycle-solution-design-domain-modeling`
- `arch-lifecycle-solution-design-biz-arch-diagramming`
- `ddd-domain-modeling-for-architecture` when domain boundaries dominate the stage

## Verification Checklist

- [ ] Business background, current state, and goals are clear
- [ ] Mature-solution reuse judgment is explicit
- [ ] Business flow / domain model / business-solution architecture are separated appropriately
- [ ] Risk and governance are not postponed into later stages
- [ ] Milestones and minimum closed loop are explicit


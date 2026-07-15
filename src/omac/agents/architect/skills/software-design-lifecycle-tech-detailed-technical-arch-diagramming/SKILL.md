---
name: software-design-lifecycle-tech-detailed-technical-arch-diagramming
description: "Use when an architect in detailed technical design must produce or review the 技术架构图 and needs the fully merged technical-architecture specialist method directly inside the new lifecycle family."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, technical-design, detailed-design, technical-architecture]
    related_skills: [arch-lifecycle-tech-detailed-methodology, arch-lifecycle-tech-detailed-core-flow-diagramming]
---

# Technical Detailed Technical Architecture Diagramming

## Overview

This skill now directly contains the merged specialist doctrine that previously lived in the legacy technical-architecture skill.

## Integrated Legacy Specialist Doctrine (Preserved and Merged)

# Technical Architecture Diagramming

## Overview

This is the architect-private skill for drawing and reviewing **技术架构图** in the **详细设计** stage.

It is deliberately built as a **two-layer skill**:

1. **Full-fidelity drawing grammar is preserved intact** in the reference file:
   - `references/universal-arch-lifecycle-tech-detailed-technical-arch-diagramming-spec-full.md`
2. **Architect-specific lifecycle method and review gates** are added in this umbrella skill:
   - where 技术架构图 sits in the architecture lifecycle
   - what detailed-design questions it must answer
   - how to separate it from neighboring artifacts
   - how to convert solution intent into concrete technical realization
   - how to review stability, operability, security, and runtime collaboration before implementation

Core principle:
**技术架构图 is not a prettier system diagram. It is the detailed-design artifact that explains how the approved solution is concretely realized through technology stack, runtime units, middleware, storage, project structure, execution paths, and reliability controls.**

This skill exists because many so-called “技术架构图” fail in one of five ways:
- they are only **system architecture diagrams** with product names sprinkled on top
- they are only **deployment diagrams**, showing hosts/clusters but not technical realization logic
- they are only **flow/sequence diagrams**, showing time order but not stable runtime structure
- they list middleware names but do not explain **why these choices exist or how components collaborate**
- they compress away drawing details, causing future diagrams to drift in shape/line/color semantics and lose consistency

Use this skill when you need a technology-realization artifact that is detailed enough to guide implementation, review, interface design, non-functional design, and operations handoff.

## Canonical Full-Fidelity Drawing Spec

Authoritative full detail:
- verbatim source draft: `references/verbatim-user-draft-技术架构图Skill.md`
- normalized working reference: `references/universal-arch-lifecycle-tech-detailed-technical-arch-diagramming-spec-full.md`

Preservation rules:
- the original user draft is preserved in verbatim form for exact wording/structure retention
- the normalized working reference exists for easier day-to-day loading while preserving the same drawing-detail payload in practical form
- this SKILL.md is an architect usage layer on top of those references, not a replacement for them
- when a future summary conflicts with the verbatim draft on drawing detail, exact wording, structure, or examples, treat the verbatim draft as authoritative
- when practical execution needs a cleaner working copy, use the normalized working reference while preserving semantics from the verbatim draft
- do **not** compress away shapes, colors, line rules, layout modes, checklists, or tool-adaptation details when actually producing diagrams from this skill

Source provenance:
- `references/source-basis.md`

Worked examples:
- `references/worked-example-backend-microservice.md`
- `references/worked-example-event-driven-pipeline.md`

## When to Use

Use when:
- the user asks for 技术架构图、详细设计中的技术架构、技术实现架构图、运行时技术图、服务/进程/任务协作图
- the solution design is already established and now must be translated into concrete technical realization
- a design doc must explain what tech stack realizes which function and why
- you need one or more diagrams to show runtime components, middleware, storage, execution paths, and stability controls
- you must review whether an existing “技术架构图” actually answers detailed-design questions instead of only looking sophisticated
- you need the full drawing grammar preserved while strengthening architect judgment around runtime, NFRs, and risk

Do not use when:
- the main question is “系统要具备哪些功能、如何分层” → use `arch-lifecycle-tech-overview-functional-arch-diagramming`
- the main question is “有哪些内部子系统、边界与外部依赖” at overview level → use `arch-lifecycle-tech-overview-system-arch-diagramming`
- the main question is “业务如何从谁流转到谁” → use business solution / process flow artifacts
- the main question is “状态如何流转、分支如何处理、补偿如何回退” → pair with state / sequence / flow diagrams
- the main question is “数据库表结构、字段、索引、ER 关系” → use data architecture / ER artifacts
- the main question is “机房、VPC、K8s 节点、网络拓扑、灰度发布路径” → use deployment / physical architecture

## Lifecycle Placement: Where This Artifact Belongs

According to the local source note `软件架构设计的生命周期`, 技术架构图 belongs to:
- **第三阶段：技术方案设计**
- specifically under **详细设计 → 技术架构**

That means the entry condition is:
- requirements research is already done
- solution design has already been reviewed
- overview design has already clarified goals, functional layering, design principles, overall system shape, and key risks

This artifact is **not** where you discover the business problem for the first time.
It is where you turn approved system/solution intent into an implementable technical structure.

### The direct lifecycle question it must answer

The local lifecycle note frames 技术架构 as the answer to:
- **系统设计中的功能通过什么技术栈实现？如何选型的？项目结构是什么样的？**
- **系统运行起来后，服务、进程、任务、调用链路如何协作，稳定性如何保障？**

Therefore, every qualified technical architecture diagram set must make these answerable quickly.

## What A Technical Architecture Diagram Must Answer

Within ~30 seconds, a reviewer should be able to infer:
1. what concrete technical roles exist
2. which layer each role belongs to
3. which runtime units actually execute work
4. which middleware / channel / store / control points are involved
5. how the main synchronous and asynchronous paths collaborate
6. where project/module boundaries or deployment carriers materially matter
7. what reliability, scaling, isolation, fallback, and observability mechanisms are built in
8. what is external dependency vs owned technical implementation
9. why the main stack choices are structurally justified

If the viewer still cannot tell:
- how the solution is implemented,
- how the system runs,
- or how it stays available under stress and failure,
then the technical architecture artifact is not finished.

## Boundary With Neighboring Artifacts

Keep 技术架构图 separate from adjacent artifacts.

### Technical architecture vs functional architecture
- **Functional architecture**: what capabilities the system should have, and how they are layered
- **Technical architecture**: how those capabilities are concretely realized by stack, engines, stores, channels, runtime units, and implementation carriers

Fast judgment rule:
- if the main nouns are **能力 / 模块 / 业务域 / 支撑域**, you are probably still in functional architecture
- if the main nouns are **网关 / 调度器 / 执行器 / 服务实例 / 队列 / 缓存 / 规则引擎 / 工作进程 / 数据存储 / 追踪 / 限流 / 熔断**, you are likely in technical architecture

### Technical architecture vs system architecture
- **System architecture**: subsystem boundary and collaboration structure at overview level
- **Technical architecture**: concrete technical realization of those subsystems

Useful test:
- if replacing “Kafka / Redis / MySQL / Worker Pool / Sidecar / Gateway / Scheduler” with generic boxes leaves the meaning almost unchanged, the current artifact is probably still system architecture, not technical architecture

### Technical architecture vs project architecture
- **Technical architecture**: runtime realization and technology stack composition
- **Project architecture**: code/module/component/dependency organization in the repository

These often relate, but they are not identical.
A strong detailed-design packet usually includes both:
- one diagram for runtime technical architecture
- one diagram for project/module structure if maintainability is a major concern

### Technical architecture vs process / state / sequence artifacts
- **Technical architecture** is stable runtime structure and collaboration topology
- **Flow/state/sequence** artifacts explain time-ordered behavior, branch handling, retries, compensation, and state closure

Important review rule:
**If the current “技术架构图” mostly shows numbered request order or branch logic over time, it is actually a dynamic behavior artifact, not a technical architecture artifact.**

### Technical architecture vs deployment / physical topology
- **Technical architecture**: what technical units exist and how they collaborate
- **Deployment topology**: where those units run physically or logically in infrastructure

Carrier information may appear in technical architecture when architecturally decisive, but the picture should still be implementation-structure-first, not rack/network-first.

## The Detailed-Design Modeling Lens

Before drawing, classify candidate content into seven buckets:

1. **Capability realization units**
   - services, engines, processors, workers, sidecars, adapters, controllers
2. **Control/governance units**
   - schedulers, config centers, rule engines, orchestration layers, control planes
3. **Execution carriers**
   - process pools, function runtimes, batch jobs, cron tasks, thread/queue workers
4. **Data/state carriers**
   - databases, caches, object stores, indexes, event logs
5. **Communication channels**
   - API gateways, message buses, MQ topics, event streams, RPC boundaries
6. **Observability and protection controls**
   - metrics, tracing, alerts, audit, circuit breaking, rate limiting, fallback path
7. **External technical dependencies**
   - external APIs, SaaS, upstream systems, identity providers, cloud capabilities

This classification prevents three common failures:
- drawing only owned business services while forgetting runtime carriers and control surfaces
- over-focusing on middleware brand names while losing collaboration logic
- mixing optional support paths and primary execution paths without hierarchy

## The Technical Architecture Decomposition Rule

When converting from approved solution/system design into technical architecture, apply this chain:

1. **Start from business/solution closed loop**
2. **Map each required responsibility to a technical role**
3. **Decide which technical roles become owned runtime units vs external dependencies**
4. **Choose communication style per edge**
   - sync request
   - async event
   - batch trigger
   - stream
   - config/control
5. **Choose state carriers**
   - durable transaction store
   - cache
   - search/index
   - object/file store
   - log/event history
6. **Add resilience controls explicitly**
   - retry
   - idempotency
   - timeout
   - rate limiting
   - circuit breaking
   - degradation
   - isolation/bulkhead
7. **Add observability and security controls explicitly**
8. **Only then optimize visual layout**

Do not start by asking “which boxes should I draw?”
Start by deciding what technical responsibilities must exist for the solution to run safely.

## Required Architect Review Gates For 技术架构图

These are the architect additions beyond generic drawing grammar.

### Gate 1: Stack-choice justification gate

Do not accept a technical architecture that only names technologies.
For every major stack choice, ask:
- what problem does this stack choice solve in this design?
- why is it better than the plausible alternatives here?
- is it solving throughput, latency, consistency, operability, delivery speed, flexibility, or isolation?
- is it a structural necessity or just familiarity bias?

Good examples:
- queue introduced to decouple peak traffic and background processing
- cache introduced to absorb high-read hot path and reduce primary-store pressure
- scheduler introduced because retries / delayed tasks / DAG dependencies require explicit control
- stream/bus introduced because many consumers require event fan-out and replay

Bad examples:
- “用 Kafka 因为常用”
- “上 K8s 因为公司都在用”
- “加 Redis 备用” with no path explanation

### Gate 2: Runtime-collaboration gate

The diagram must make runtime collaboration visible.
Ask explicitly:
- what runs continuously?
- what runs on trigger?
- what runs on schedule?
- what runs in parallel?
- where are queues, pools, or execution windows?
- what blocks synchronously vs what drains asynchronously?

If a service graph exists but no reviewer can tell how work actually gets executed after request arrival, the diagram is incomplete.

### Gate 3: Main-path vs side-path gate

Separate these visually:
- main success path
- async/offline path
- fallback / degradation path
- observability/control path

Do not give monitoring, audit, retries, and admin control the same visual weight as the user-facing hot path.
Likewise, do not hide critical degrade/fallback paths in prose only.

Additional detailed-design rule:
- if the viewer’s first impression is "this is a flowchart" rather than "these are the stable technical planes / layers / runtime units", the diagram is failing as a 技术架构图
- for detailed technical architecture, make **planes/layers the primary visual structure** and keep arrows secondary
- prefer naming and grouping such as `Source Acquisition Plane`, `Execution Plane`, `Control Plane`, `Serving Plane`, `Replica Plane`, `Observability/Governance Plane`
- if you need many labels on arrows to explain the picture, too much runtime narrative has leaked into the architecture view; move that content into 核心流程图 / 时序图 instead
- a good test: after hiding most arrow labels, the reader should still understand the system structure from the planes and runtime units alone

### Gate 4: Reliability-control gate

At technical-design stage, stability must appear in the structure.
Check whether the diagram explicitly or companion-note explicitly shows:
- timeout boundary
- retry strategy
- idempotency boundary
- circuit break / degrade path
- queue backlog absorption or backpressure handling
- failover / replica / redundancy
- data consistency strategy
- hot path bottleneck isolation

If “高可用” appears only as a sentence below the diagram, the technical architecture has not actually expressed it.

### Gate 5: Data-state ownership gate

Do not blur state ownership.
Ask:
- which component is source-of-truth for each important state?
- what is transient vs durable?
- what is cache vs transaction store vs analytics/search projection?
- which path requires strong consistency and which accepts eventual consistency?
- where do replay/rebuild/compensation rely on persisted history?

If multiple components seem to own the same truth with no governance, the architecture likely has hidden consistency risk.

### Gate 6: External-dependency unreliability gate

Carry forward the lifecycle rule that collaborators must often be treated as unreliable.
For each external dependency, ask:
- what happens on timeout, error, stale data, partial response, rate limit, auth failure?
- do we fail closed, fail open, queue for later, or switch to manual handling?
- is there isolation so one external fault does not collapse the whole core path?

If the answer materially affects structure, represent it visually using fallback edges, side queues, governance nodes, or callouts.

### Gate 7: Security and compliance gate

If the system touches high-risk domains, make sure the technical architecture reflects:
- authentication / identity source
- authorization boundary
- secret / key management boundary
- audit trail path
- sensitive-data classification or redaction point
- privileged cross-domain access path

Do not wait until a separate security appendix to realize the runtime path is missing critical controls.

### Gate 8: Operability gate

Ask whether an operator or SRE can infer from the technical architecture:
- where to observe health
- where to find failure signal
- where to throttle, pause, reroute, or replay
- which components can scale independently
- which components are single points of failure
- what recovery path exists after backlog, corruption, or external outage

If none of that can be inferred, the diagram may be technically correct but operationally weak.

## How To Use The Full Drawing Spec In Practice

When actually producing diagrams, do not rely on this umbrella alone.
Load and apply the full reference for:
- architecture viewpoints
- layout mode selection
- shape semantics
- line semantics
- color semantics
- typography / spacing / alignment
- anti-pattern detection
- multi-tool adaptation and downgrade strategy
- final checklist

The umbrella skill decides **what the artifact must answer**.
The reference decides **how the diagram must be drawn in full detail**.

### Which reference to load first

Use this routing:
- if the task is mainly preserving visual grammar and drawing consistency → load `references/universal-arch-lifecycle-tech-detailed-technical-arch-diagramming-spec-full.md`
- if the task is mainly a classic transactional backend / layered service system → also load `references/worked-example-backend-microservice.md`
- if the task is mainly event-driven / async processing / queue-stream pipeline → also load `references/worked-example-event-driven-pipeline.md`
- if the task mixes multiple concerns, start from the full spec, then add the closest worked example as the execution template

## Practical Drawing Workflow For 技术架构图

### Step 0: Confirm lifecycle readiness

Before drawing, verify:
- business problem and solution scope are already clear
- overview design already exists or is at least mentally stable
- the main technical unknowns are implementation-shape unknowns, not problem-definition unknowns

If not, do not fake detailed design yet.
Return to solution/system design clarification.

### Step 1: State the technical design target in one sentence

Write:
- **本图将说明：该方案通过哪些技术栈、运行单元、存储与控制机制实现，以及运行时如何协作并保证稳定性。**

This sentence is the anchor. If the diagram drifts into capability listing or physical topology, return to this target.

### Step 2: Extract the implementation responsibilities

From the approved solution/system design, enumerate:
- request entry points
- control/orchestration points
- execution units
- storage/state carriers
- integration channels
- observability and security controls
- external dependencies

Do not name products first.
Name the technical roles first.

### Step 3: Decide the dominant viewpoint and layout

Typical defaults:
- **逻辑 + 运行时** for service/collaboration-heavy systems
- **逻辑 + 数据** for data-intensive platforms
- **逻辑 + 安全** when trust boundaries dominate
- **逻辑 + 部署** only when carrier placement is architecturally decisive

Typical layouts:
- **Tier Stack** for most backends and platforms
- **Pipeline** for ETL / CI-CD / ML / staged processing
- **Hub-Spoke** for gateway / bus / central control plane systems
- **Concentric Zone** for zero-trust / DMZ-heavy systems
- **Mesh** for service-mesh data plane / P2P / blockchain-like peers

#### Anti-flowchart checkpoint (mandatory)

Before drawing, explicitly ask: **is this picture trying to explain static runtime structure, or is it secretly explaining step order?**

If the picture is for 技术架构图, default to these visual choices:
- start from **system boundary + functional planes / domains**, not from a top-to-bottom request path
- place **external actors / dependencies outside** the owned runtime boundary
- put related technical units inside **containers / planes** first, then draw relations
- use node names that read like **stable technical roles** (`gateway`, `adapter`, `repository`, `worker runtime`, `control plane`) rather than step labels (`receive`, `parse`, `dispatch`, `return`)
- make arrows express **dependency / data path / control path / governance path**, not numbered or implied time sequence
- if a linear main path dominates the viewer's first impression, split that path into a separate 核心流程图 instead of forcing it into the 技术架构图
- when a reviewer says “this still looks like a flowchart”, treat that as a layout failure, not a wording failure: reduce narrative arrows, strengthen plane/container grouping, and if possible benchmark against an already-approved sibling-project architecture diagram in the same document ecosystem

Fast smell test:
- if the eye naturally reads the picture as `A then B then C then D`, it is drifting toward a flowchart
- if the eye first sees `which planes exist, what each plane owns, and how planes relate`, it is behaving like an architecture diagram

For concrete smells and rewrites, see `references/architecture-vs-flowchart-smells.md`.

### Step 4: Map each role to concrete technical realization

Now name concrete choices where appropriate:
- gateway type
- scheduler / workflow engine
- worker pool / execution model
- queue / bus / stream
- cache / DB / object store / index
- observability stack
- auth / secret / audit controls

Rule:
Use product names when they matter architecturally.
Do not add version noise or config minutiae unless the choice itself is the design issue.

### Step 5: Draw the main path first

Draw only the dominant success path:
- request enters
- orchestration/control decides
- execution units process
- state is read/written
- result returns or event is emitted

Make this legible before adding support complexity.
If the main path is messy, the final diagram will collapse under added detail.

### Step 6: Layer in async, fallback, governance, and observability paths

Add, in this order:
1. async side paths
2. scheduled/batch paths
3. fallback/degrade paths
4. monitoring/audit/alert paths
5. security boundary annotations

Do not add all edge types at once.
Preserve visual hierarchy.

### Step 7: Mark the stability and scaling semantics

Use the full reference grammar to explicitly show:
- multi-instance / replica / scale-out
- load balancing or dispatch strategy
- queue buffering / partitioning / fan-out
- retry loop or compensation
- cache or read-replica path
- isolation lane / tenant / AZ / security zone
- metrics / tracing / alert route

This is where technical architecture stops being a component inventory and becomes an engineering design artifact.

### Step 8: Decide whether one diagram is enough

Split when needed:
- overall technical architecture
- runtime/process collaboration view
- data architecture view
- security technical view
- project/module structure view

Hard rule:
One diagram should answer one dominant technical question.
If it tries to answer all of runtime, data, security, deployment, and project structure at once, split it.

## What To Include In Companion Notes Around The Diagram

A strong technical architecture diagram is usually accompanied by short notes on:
- major stack choices and why
- critical runtime assumptions
- consistency model
- timeout/retry/idempotency rules
- scale bottlenecks and mitigation
- fallback and manual intervention path
- security/audit assumptions
- operational verification pointers

Do not overload the diagram with paragraphs.
Put the minimum visible semantics on the diagram and move explanation into short callouts or the surrounding design text.

## Readability And Render-Safety Gates

Before finalizing any technical architecture diagram, perform two explicit checks:

### A. Readability gate
- main path should be identifiable in one glance
- control-plane lines should not weave through the primary data path
- side paths such as observability, rollback, or replica sync should be visually separated rather than crossing the hot path repeatedly
- if lines still look tangled after one pass, change the layout instead of only nudging coordinates
- prefer left-to-right / top-to-bottom monotonic routing for the dominant path

### B. Render-safety gate
- if emitting raw SVG/HTML/XML-based diagram assets, escape XML-sensitive text in labels (`&`, `<`, `>`, quotes in attributes where relevant)
- validate the final artifact as parseable XML before handoff when the carrier is SVG
- a diagram that is visually well designed but not well-formed XML is a delivery failure, not a minor polish issue

Practical rule:
- if a label contains `&`, write `&amp;` in SVG text nodes

## Common Technical-Architecture Smells

1. **Technology logo collage**
   - many products named, no collaboration logic
   - fix: redraw around responsibilities and paths first

2. **System architecture with product paint**
   - same overview boxes, only product names changed
   - fix: add execution carriers, channels, stores, runtime paths, reliability controls

3. **Everything synchronous**
   - every edge looks like RPC
   - fix: distinguish async/event/batch/control paths explicitly

4. **Storage as a black hole**
   - one giant database box with no ownership semantics
   - fix: separate transactional, cache, object, index, queue, audit/history roles

5. **No failure semantics**
   - hot path exists, but retry/degrade/timeout/isolation absent
   - fix: add visible reliability structure

6. **Observability omitted**
   - no metrics, logs, traces, alerts, or audit path shown
   - fix: add watch/alert/tracing links or explicit observability layer

7. **Deployment detail overload**
   - pods, IPs, zones, ports swamp the technical story
   - fix: move physical detail to deployment view unless architecturally decisive

8. **Project structure confused with runtime structure**
   - repo modules are drawn as if they are deployed runtime nodes
   - fix: split project architecture and runtime technical architecture

9. **Unjustified middleware introduction**
   - queue, cache, workflow engine, or search engine present with no structural reason
   - fix: annotate the problem solved by each major technical choice

10. **One diagram answers everything badly**
   - runtime, state, security, deployment, and process all crammed together
   - fix: split by dominant concern

11. **Messy crossing lines in the hot path**
   - the main value of the diagram disappears because data path, control path, and side paths intersect repeatedly
   - fix: re-layout by path ownership, not by box symmetry

12. **Raw SVG labels that break XML rendering**
   - labels include unescaped `&` or other XML-sensitive characters, causing the whole diagram not to render
   - fix: escape text and validate the SVG parses successfully before delivery

13. **Technical architecture drawn as a process storyboard**
   - left-to-right request/batch narration dominates, with planes reduced to decoration
   - symptoms: every important sentence lives on an arrow label; replica/query/governance paths are drawn as route continuations of the main path; the viewer reads steps before they read structure
   - fix: redesign around stable planes/layers first, then keep only a few decisive relationships; move detailed execution storytelling into core-flow / sequence artifacts

## Verification Checklist

Before considering the skill output complete, verify:

- [ ] The artifact is clearly in **详细设计 / 技术架构** stage, not still overview design
- [ ] The diagram directly answers how functionality is realized by concrete technical stack and runtime structure
- [ ] The boundary from 功能架构图 / 系统架构图 / 流程图 / 部署图 is explicit
- [ ] Main path, async path, fallback path, and observability/control path are visually distinguishable where relevant
- [ ] Stack choices are justified by structure, not only named
- [ ] Runtime execution units are visible: service, worker, scheduler, pool, function, job, or equivalent
- [ ] State ownership is clear: DB/cache/object/index/queue/history roles are not collapsed blindly
- [ ] Stability controls are visible: retry, idempotency, degrade, isolation, replica, throttling, backpressure, or equivalent
- [ ] Security / audit / auth controls appear when materially relevant
- [ ] If the picture became overloaded, it has been split into multiple views
- [ ] The full drawing grammar from `references/universal-arch-lifecycle-tech-detailed-technical-arch-diagramming-spec-full.md` was consulted rather than summarized from memory
- [ ] A reviewer could explain the technical realization in under ~30 seconds after seeing the diagram

## Common Pitfalls

1. **Skipping the full reference because the umbrella seems enough.**
   This loses the user-mandated preservation of drawing detail. Always consult the reference when producing the real artifact.

2. **Calling something 技术架构图 when it is only 项目结构图.**
   Code-package layout may matter, but it does not replace runtime technical architecture.

3. **Showing only product names with no relation semantics.**
   The point is not to prove you know tools; it is to show why the system works.

4. **Adding middleware before proving the path needs it.**
   Technical architecture should reduce implementation ambiguity, not add novelty for its own sake.

5. **Forgetting external unreliability.**
   External dependencies often need isolation/degrade/manual handling paths visible in the design.

6. **No distinction between control plane and execution plane.**
   In many systems, config/rules/orchestration and runtime execution must be visually separated.

7. **Not splitting diagrams when data/security/runtime concerns diverge.**
   Overloaded diagrams create false completeness while reducing actual clarity.

## Worked Example Usage Notes

The worked examples are not copy-paste templates for product names.
They are meant to teach future agents:
- what the dominant path usually looks like
- what technical roles are easy to forget
- which reliability controls should be visible
- where one diagram should split into multiple views

When using a worked example:
- keep the structure and review logic
- replace example labels with scenario-true labels
- do not import details that are not structurally justified by the current system
- if the real system is hybrid, combine the example only after the primary layout/viewpoint is decided from the full spec

## Recommended Companion Skill Routing

Pair this skill with:
- `arch-lifecycle-delivery` for top-level staged delivery and review chain
- `arch-lifecycle-tech-overview-functional-arch-diagramming` when capability layering is still unclear
- `arch-lifecycle-tech-overview-system-arch-diagramming` when subsystem boundaries and dependencies need separate clarification
- `ddd-domain-modeling-for-architecture` when semantic or consistency boundaries drive the technical realization

## Remember

A good 技术架构图 does not merely say **what technologies exist**.
It shows **how the approved solution becomes a running system**, **why the main technical choices exist**, and **how the system remains understandable, stable, and operable under real conditions**.


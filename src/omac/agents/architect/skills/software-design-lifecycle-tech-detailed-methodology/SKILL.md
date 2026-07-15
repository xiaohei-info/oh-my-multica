---
name: software-design-lifecycle-tech-detailed-methodology
description: Use when an architect must run the detailed-design portion of technical
  design, preserve the full lifecycle-stage doctrine for detailed design, and define
  the concrete runtime, project, flow, data, interface, non-functional, security,
  recovery, hardware, and implementation-plan surfaces needed for execution.
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags:
    - architect
    - lifecycle
    - technical-design
    - detailed-design
    - implementation
    related_skills:
    - arch-lifecycle-delivery
    - arch-lifecycle-tech-detailed-technical-arch-diagramming
    - arch-lifecycle-tech-detailed-core-flow-diagramming
    - arch-lifecycle-tech-overview-methodology
---


# Technical Detailed Methodology

## Overview

This is the `architect` profile's **技术方案设计 → 详细设计 方法论 skill**.

It is the second half of technical design and should only start on top of a stable overview design.

The detailed-design stage is now intentionally kept **leaner**:
- keep separate specialist companion skills only for the two highest-value recurring artifact classes
  - 技术架构图
  - 关键流程图 / 状态机
- keep the other detailed-design surfaces inside this methodology skill as part of one integrated detailed-design packet

That keeps the live skill family clearer and avoids over-splitting the stage into too many tiny children.

## When to Use

Use when:
- solution design and overview design have already been reviewed
- the architect must define concrete technical realization surfaces for implementation
- the task is about 技术架构、项目架构、关键流程、数据结构、接口、非功能需求、安全、备份恢复、风险、硬件、实施计划

## Canonical Stage Doctrine (Full Preservation)

| 详细设计 | 技术架构   | 系统设计中的功能通过什么技术栈实现？如何选型的？项目结构是什么样的？ <br>系统运行起来后，服务、进程、任务、调用链路如何协作，稳定性如何保障？<br><br>_依赖与组件等实现方式，基于需求调研中得到的业务解决方案，合理利用技术栈、工具链、中间件的组合、以实施落地为目的对该业务方案进技术细化_<br> | 技术架构图，示例如下： <br>![](../../../../repository/images/软件架构设计的生命周期-1779158661450.png)                                                                                                          |
|      | 项目架构   | 代码、模块、组件、依赖如何组织，如何保证可维护性与可扩展性？<br><br>*项目结构、模块分层、组件依赖、技术选型说明*                                                                                             | 项目架构图                                                                                                                                                                                     |
|      | 关键流程   | 有哪些关键业务流程？通过什么图表来体现？ <br><br>_不同角色不同阶段的处理过程，以及各种分支情况的条件及处理逻辑，要覆盖所有流程分支，不重不漏_                                                                              | 状态机，示例如下：  <br>![](../../../../repository/images/软件架构设计的生命周期-1779158733831.png)流程图，示例如下：<br>![](../../../../repository/images/软件架构设计的生命周期-1779158753327.png)                              |
|      | 数据结构   | 程序中流转的数据是怎么定义的？                                                                                                                                           |                                                                                                                                                                                           |
|      |        | 与外部系统交互的接口数据是怎么定义的？                                                                                                                                       |                                                                                                                                                                                           |
|      | 数据架构   | 需要什么样的数据、如何存储与使用？ <br><br>*存储到数据库中的表结构定义，主键、字段类型、字段含义、唯一键、索引等 *                                                                                           | ER图（drawio-skill / draw.io 源文件）                                                                                                                                                                            |
|      |        | 数据一致性、唯一性、索引设计、冷热分层、归档清理、备份恢复、敏感数据保护如何设计？                                                                                                                 | 数据约束说明                                                                                                                                                                                    |
|      | 系统接口   | 对外接口如何定义？ _入参、出参、幂等、异常、结果码、超时、重试、限流、鉴权、审计要求_                                                                                                              | 接口定义文档                                                                                                                                                                                    |
|      | 非功能性需求 | 有哪些系统监控、业务监控、数据监控需求？核心指标、告警阈值、通知方式、响应人、处理时限分别是什么？                                                                                                         | 监控告警清单                                                                                                                                                                                    |
|      |        | 吞吐、延迟、并发、容量目标分别是什么？压测口径、验收标准是什么？                                                                                                                          | 性能指标与压测方案                                                                                                                                                                                 |
|      |        | 是否需要限流、熔断、降级、隔离、容灾、扩缩容能力？故障时如何保证核心服务可用？                                                                                                                   | 高可用方案                                                                                                                                                                                     |
|      |        | 鉴权、授权、数据脱敏、密钥管理、审计留痕、防刷、防重、防越权分别如何设计？                                                                                                                     | 安全设计说明                                                                                                                                                                                    |
|      |        | 需要备份哪些数据？恢复点目标（RPO）与恢复时间目标（RTO）是什么？恢复流程如何验证？                                                                                                              | 备份恢复方案                                                                                                                                                                                    |
|      |        | 当前已知风险有哪些？触发条件、影响范围、预防措施、应急预案分别是什么？                                                                                                                       | 风险清单                                                                                                                                                                                      |
|      | 硬件需求   | 需要哪些、什么样的硬件部署服务？ _硬件、网络、机器、节点、拓扑等_                                                                                                                        |                                                                                                                                                                                           |
|      | 实施计划   | 人力安排、团队配置、开发周期、测试周期、联调周期等细项                                                                                                                               |                                                                                                                                                                                           |

## Architect Execution Layer

- make the system implementable, not only understandable
- keep runtime structure, code structure, dynamic flows, data structures, interfaces, and NFR controls distinguishable
- use separate artifacts when one view cannot answer all detailed questions cleanly
- do not hide reliability, recovery, or security assumptions in vague prose

## Recommended Detailed-Design Packet

### Keep as separate specialist skills
These two are worth separate live skills because they are recurring, visually intensive, and easy to confuse with neighboring artifact types:
- `arch-lifecycle-tech-detailed-technical-arch-diagramming`
- `arch-lifecycle-tech-detailed-core-flow-diagramming`

### Keep inside this methodology skill
These usually do **not** need separate live skills by default unless a future workload proves they recur independently often enough:
- 项目架构
- 数据结构 / 数据架构
- 系统接口
- 非功能性需求
- 安全 / 高可用 / 备份恢复 / 风险
- 硬件需求 / 实施计划

Reason:
- they are important surfaces
- but they are often better handled as one integrated detailed-design packet than as many tiny sibling skills
- splitting them too far makes the family harder to load and reason about

## Practical Subsections To Cover Inside This Stage

### 1. 项目架构
Answer:
- how code / modules / components / dependencies should be organized
- how maintainability and extensibility are protected structurally

### 2. 数据结构 / 数据架构
Answer:
- what data objects move through the program
- what external interaction structures look like
- what persistent structures exist
- what consistency / indexing / archival / backup / sensitive-data rules apply

### 2A. Cross-module boundary tightening rules (high-value review TODO pattern)
When iterating a subsystem detailed-design document from reviewer TODOs, do not only “answer the comment”; use the TODO to check whether a diagram, object, or flow leaked across module boundaries.

Default tightening rules:
- if a flow is documenting **ETL / pipeline execution**, keep **query/read-definition concerns out of that flow** unless the query layer is a direct runtime participant
- if a release / rollback diagram is about **family-definition or pipeline-definition switching**, name the downstream runtime precisely (for example, later pipeline batches / replay batches) rather than a vague cross-layer label like “query layer”
- if a control-plane object belongs to a higher module, move it back to that module’s document instead of letting the current module absorb it “for completeness”
- when a reviewer says “this looks like a higher-layer concern”, prefer **scope reduction** over adding more explanatory arrows

Typical examples worth preserving:
- `Family Definition` onboarding / release belongs to the ETL / pipeline side; `Read API Definition` belongs to the query-serving side unless the current module truly governs both
- a subsystem-level detailed design may mention adjacent layers for context, but its dynamic diagrams should still track the module that actually executes the path

### 2B. Transport envelope vs external payload-reference rule
Do not default queue/event examples to `payload_ref` object-storage indirection.

Prefer this order:
1. first design the collector / batcher so one queue message stays within the transport limit
2. keep the real payload inline in the event envelope when that is operationally reasonable
3. use a separate object-reference indirection only as an explicit oversized-payload exception path, not as the default shape

Why:
- it keeps the transport contract simpler
- it avoids inventing a second truth hop for ordinary messages
- it makes replay, inspection, and definition onboarding examples more concrete

If inline payload is chosen, still carry a digest / stable id for idempotency and audit.

### 2C. Analytical replica sync modeling rule
When a local or edge analytical replica can connect directly to the upstream warehouse, model the sync path as a **thin sync executor/config layer** first; do not over-design exporter/importer/file-hop stages unless they are actually required.

Preferred expression in detailed design:
- state clearly whether the replica is **full snapshot** or incremental
- state exactly which upstream layers/tables are mirrored (for example raw + stage + governed views)
- keep the executor role thin: credentials injection, attach/config, sync SQL/materialization, watermark recording
- keep the replica out of the public serving hot path unless it is truly the serving backend

### 3. 系统接口
Answer:
- inputs / outputs
- idempotency
- exceptions / result codes
- timeout / retry / rate-limit
- auth / audit requirements

### 4. 非功能性需求 / 安全 / 可靠性 / 恢复
Answer:
- monitoring / alerting
- throughput / latency / concurrency / capacity
- HA / resilience / degrade / isolation / DR
- authz / authn / masking / key management / audit
- backup / RPO / RTO / restore validation
- known risks and emergency handling

### 5. 硬件需求 / 实施计划
Answer:
- hardware / network / machine / node / topology assumptions
- staffing / team configuration
- development / testing / integration schedule details

## Companion Diagram / Artifact Skills

- `arch-lifecycle-tech-detailed-technical-arch-diagramming`
- `arch-lifecycle-tech-detailed-core-flow-diagramming`

## Practical handoff pattern: overview design → detailed design packet

### Runtime-backed product delivery packet rule (development-entry version)
When the target is a **runtime-backed product/control plane** (for example, a Team Panel + Gateway layer built on top of an existing agent runtime/web-console base), and the user explicitly asks for a **complete document system that lets the development team enter implementation**, do not stop at a single detailed-design master note plus a vague “later split into subdocs” section.

### Downstream implementation-carrier calibration rule
When the product is going to be implemented as a **downstream derivative project** of an upstream web UI / runtime-console base (for example, a new `Agent Service` repo built by secondary development on top of an existing `hermes-webui` codebase), detailed design must explicitly calibrate to the **real implementation carrier**, not only to the upstream source of inspiration.

Required tightening actions:
1. name the **actual target repo/project** and, when known, its canonical path
2. distinguish clearly between:
   - **upstream source base** — what is being reused or borrowed from
   - **downstream implementation carrier** — where the new product will actually be built
3. state whether the major layers (`Team Panel`, `Agent Gateway`, etc.) are:
   - internal modules in the same project/process, or
   - separately deployed services
   Do not let readers infer microservice boundaries that do not exist yet.
4. when V1 is same-process, say so explicitly using wording like:
   - shared `server.py` / shared host process
   - `api/routes.py` grows by addition, not replacement
   - runtime-adapter logic is extracted/parameterized from existing host modules rather than launched as a new network hop
5. preserve the concrete reuse anchors (`server.py`, `api/routes.py`, `api/streaming.py`, `api/config.py`, `api/profiles.py`, `static/*`, etc.) so implementers know exactly which inherited files are substrate versus which new directories are net-new business modules
6. if the control-plane database truth differs from the inherited host's JSON/session storage model, state the dual-track rule explicitly (for example: control-plane truth in PostgreSQL; host/session mirror remains in JSON/session storage for runtime/workbench compatibility)

Fast review test:
- if the doc still reads as though the team will implement directly inside the upstream project when the real target is a derivative repo, it is not calibrated enough
- if readers could mistake two internal modules for two deployed services, the wording is too loose
- if the doc says “reuse WebUI” but does not say which existing files remain the host substrate and which new module paths carry business logic, it is not implementation-guiding yet

See also `references/runtime-backed-derivative-project-calibration.md` for a compact example and wording checklist covering upstream base vs downstream implementation carrier, same-process module boundaries, reuse anchors, and dual-track control-plane vs host/session storage truth.

Default delivery packet for this class of system:
1. **One subsystem-level detailed-design hub/master document**
   - freezes top-level boundaries, shared object mapping, main control flow, and global implementation constraints
2. **Four baseline child module detailed-design documents**
   - **data truth**: domain model + data architecture + tables/indexes/state semantics
   - **runtime truth**: gateway/runtime adaptation + runtime handles + event hydration/reconcile + credential resolution
   - **flow truth**: private chat / group chat / orchestration / scheduled-loop lifecycle closure and exception branches
   - **interface truth**: page groups + northbound APIs + SSE/timeline contract + pagination/filter/permission/empty-error states
3. **Optional fifth child document: control-plane internal implementation design**
   - add this when the product/control plane has substantial backend logic of its own and the four baseline docs still do not answer:
     - service/router/repository/integration layering
     - transaction boundaries on key write paths
     - query-side aggregate view / read-model construction
     - permission-check placement
     - internal control-plane -> gateway call boundary
   - recommended role label: **内部实现口径文档**
   - recommended scope: internal services + aggregate views / read models, not a duplicate of data/flow/interface docs
4. **One directory/index note**
   - tells the team which document to read first and which document is authoritative for each class of implementation question
5. **Parent-hub cleanup pass**
   - replace any “future split / later child docs / suggested subdocs” wording in the hub with the canonical child-doc list once those child docs actually exist

Why this matters:
- it turns a good design note into a true development-entry packet
- it prevents the common failure mode where the master note is strong but table design / runtime adaptation / UI contract are still scattered or implied
- it gives different implementation roles (backend, gateway/runtime, frontend, QA) an explicit authoritative landing point

Fast review test:
- if a developer still asks “where is the table truth?”, “where is the runtime-handle/event-replay truth?”, or “which doc owns page/API/SSE contract?” then the packet is not yet development-entry ready
- if the master doc still says child docs are only recommendations after the child docs were already written, the packet is not properly closed

When this delivery-packet rule applies, prefer writing the child docs in this order:
1. data truth
2. runtime truth
3. flow truth
4. interface truth

Then update the hub and index note in the same round so the document set is self-consistent.

When a subsystem already has an approved/usable 概要设计 and the next user ask is “start detailed design now”, prefer this execution order:

1. **Read the overview design first and inherit its fixed boundaries.**
   Do not rediscover the system from scratch unless the overview is clearly unstable.
2. **Check whether the target directory already contains child-module detailed-design placeholders.**
   If they exist but are empty or near-empty, do **not** rush to fill all child docs first.
3. **Write a subsystem-level detailed-design hub document first.**
   This hub should answer the cross-module questions that implementation cannot avoid:
   - runtime technical architecture
   - main data/control flows
   - core objects / envelopes / enums and their execution semantics
   - upstream/downstream integration list
   - auth / connection paths
   - NFR / rollback / observability / recovery baseline
4. **Co-locate the key diagrams with that hub document first.**
   At minimum, produce:
   - 技术架构图
   - 核心流程图
   Add state/data-flow views only when they reduce ambiguity.
5. **Only after the hub stabilizes, split into child module detailed-design docs.**
   This avoids writing four inconsistent module docs while the shared runtime model is still moving.
6. **When landing child docs into pre-created module folders, use explicit artifact names and migrate links cleanly.**
   Preferred pattern:
   - place each child doc inside its module directory
   - name it as `<module>-详细设计文档` when the workspace/user wants the artifact level made explicit
   - update the subsystem hub note to point to the new canonical child-doc names
   - leave the old bare-name placeholder note as a tiny redirect/stub instead of keeping an empty file or duplicating content

Why this pattern works:
- it preserves implementation-level concreteness without fragmenting the design too early
- it keeps shared contracts and runtime semantics in one place first
- it gives later module docs a stable parent packet to inherit from
- it avoids broken wiki links and avoids confusing a folder placeholder with the real module-level detailed-design artifact

## Runtime-backed product/control-plane mapping rule

### 2D. PRD ingestion boundary rule for detailed design
When a subsystem detailed-design document is downstream of both:
- a **business solution / solution design** document, and
- an **overview technical design** document,

treat the PRD / prototype / page-spec corpus as a **coverage and interaction reference only**, not as the authority for backend truth.

Default rule:
- use PRD/prototypes to confirm page inventory, user-visible actions, and completeness of functional coverage
- do **not** lift PRD-provided field lists, interface shapes, table names, pseudo-APIs, transaction claims, or implementation notes directly into detailed design unless they are independently justified by the service-side design
- backend/domain/data/interface truth in detailed design must inherit from the business solution + overview design + verified runtime/source constraints

Common smells that mean the detailed-design doc is drifting into PRD-backed server fiction:
- page-facing endpoints from a prototype are written as if they were the northbound service contract
- PRD table names or field packs appear without any mapping back to runtime/domain ownership
- UI interaction terms are used to define server objects instead of product/domain concepts

Tightening pattern:
1. preserve the PRD's page/function coverage signal
2. restate the server-side object in domain terms
3. map that object to the verified control-plane / gateway / runtime contract
4. if needed, leave page-level field/interface detail for a later front-end/API contract doc instead of polluting the subsystem master detailed-design doc

### 2E. Northbound contract vs reused-host-route rule
When the product reuses an existing Web UI / host backend that already exposes working routes such as `/api/chat/start` or `/api/chat/stream`, do **not** let those host routes become the main northbound contract in the product detailed-design narrative unless the product truly intends to expose them as its stable product-facing boundary.

Prefer this layering:
- product/control-plane doc: describe the **business request object** and the **gateway/internal adapter capability** (for example `start_single_agent_run(run_request)`)
- reused host/backend doc or implementation notes: mention that V1 may internally reuse existing host routes / SSE scaffolding / session hosting code
- runtime layer: document the real runtime call path (`run_conversation`, kanban dispatch, cron job creation, etc.)

Fast review test:
- if removing the reused host app would force the business contract section to be rewritten, the contract is too coupled to implementation detail
- if the product story still makes sense when the internal host route is swapped out for another adapter, the abstraction level is probably correct

### 2F. Blueprint object vs execution-workflow rule
Do not automatically equate a product-side **solution / blueprint / package** object with a runtime **workflow** object.

Preferred distinction:
- blueprint object: a reusable business package or application bundle (templates, defaults, bindings, recommended roster, default policies)
- execution workflow: the runtime collaboration/execution strategy used when a live task runs

Safe mapping:
- a blueprint may carry an **optional default collaboration template / default roster / default orchestration policy reference**
- but the blueprint itself should not be declared identical to the workflow unless the product intentionally has a single merged concept

Why this matters:
- it prevents the product catalog layer from collapsing into runtime-engine semantics
- it keeps one-click application / provisioning logic separate from live task execution logic
- it makes later replacement of orchestration strategy possible without redefining the product object itself

See also `references/unblock-criteria-for-development-entry.md` for the 5 concrete criteria that must be satisfied before a `block` verdict can be downgraded to `revise` or `pass with comments`.

See also `references/runtime-backed-detailed-design-delivery-pass.md` for the final tightening checklist that turns a runtime-backed product detailed-design master doc from “good structure” into a delivery-ready implementation packet.

### 2G. Delivery-ready tightening pass for runtime-backed product detailed design
When the user asks to keep iterating a subsystem detailed-design master document until it is truly deliverable, do not stop after object mapping and happy-path flows feel conceptually correct. Run a final delivery pass and explicitly check whether the document now contains the minimum execution packet below.

For runtime-backed product/control-plane documents, the final pass should usually add or tighten these surfaces:
- a concise **runtime/control-plane structure skeleton** showing Team Panel / control plane, gateway adapter, reused host shell, runtime, and external capabilities as separate layers
- a **runtime handle** object (or equivalent) that defines the smallest join key set used to reconcile business objects with runtime truth (`profile_name`, `session_id`, `task_id`, `job_id`, cursors, etc.)
- a **timeline/event envelope** for streamed or replayable execution events, plus a minimum event-type enum list
- **status enums with execution semantics**, not just field names, for the core business objects (`Run`, `Conversation`, `ScheduledJob`, etc.)
- a **credential/auth resolution path** that explains how business-side connector grants become runtime-visible credentials without making the browser or product DB the secret truth source
- a **failure / retry / compensation / reconnect** section covering idempotency, partial acceptance, stream interruption, worker failure, and scheduled-job failure closure
- **northbound API examples** at the product boundary plus the internal adapter boundary, so the contract level is concrete without collapsing into reused-host internal routes
- **NFR / security / backup / reconcile** requirements with explicit minimum tags/keys needed for audit and state recovery
- a **recommended implementation order and acceptance checklist** so the document can guide phased delivery instead of only static design review

Fast review test:
- if a reviewer still asks “how do I reconcile UI state with runtime authority / shared contract when streams break?” the master doc is not ready
- if the doc still has only object names but no runtime handle / event envelope / failure closure, it is not ready
- if the doc explains architecture correctly but gives no phase order or acceptance bar, it is still a design note rather than a delivery packet

### 2H. Design-document code-verification rule for detailed-design review

When reviewing a detailed-design document that cites an existing codebase as a reference, reuse target, or source of truth, do not review the design in isolation. Cross-reference every capability claim against the actual source code.

Default verification protocol:
1. identify every explicit code reference in the design doc (file paths, module names, function names, API endpoints)
2. open each referenced source file and verify the claimed capability actually exists
3. flag any mismatch between the design's assumption and the code's reality
4. classify each mismatch as: (a) capability exists but is mischaracterized, (b) capability does not exist and must be built, (c) capability exists but has a different interface/shape than assumed

This prevents a common failure mode: design docs that claim "复用 hermes-webui 的 SSE 流能力" when the actual source implements a memory-queue based push model with no event persistence, no cursor mechanism, and no replay capability — and the design requires all three.

### 2I. Hot-path persistence throughput rule

When a detailed design places database write operations in an event-streaming or real-time delivery hot path (e.g., persisting every token/delta event to a relational database before or during SSE push), the design must explicitly address:

1. **Sync vs async**: does the write block the delivery path?
2. **Batch strategy**: batch size, flush interval, and whether batching is acceptable for the latency target
3. **Backpressure**: queue bounds, overflow behavior, and degradation strategy when writes fall behind
4. **Component choice**: whether a relational DB is the right persistence layer for this throughput, or whether a lighter buffer (in-memory queue, Redis Stream, append-only log) should precede it

A design that says "写入 Event Store" and stops there is incomplete for event-streaming scenarios. At minimum, the design should state: "V1 采用内存队列 + PG 批量写入" with explicit batch parameters and the rationale for not introducing additional infrastructure.
When producing Chinese detailed-design artifacts, prefer `口径` / `权威口径` / `共享口径` over casually using `真相` as a formal document term.

Use this rule especially when freezing cross-module contracts such as:
- event protocol
- cursor format
- state-machine semantics
- northbound API payloads
- role / permission model

Recommended wording:
- `共享运行口径冻结版`
- `数据口径文档`
- `运行口径文档`
- `流程口径文档`
- `接口口径文档`

Avoid mixing `真相 / 口径 / source of truth` randomly across sibling Chinese documents. If the packet is already partly written with `真相`, run a terminology normalization pass before declaring the document set ready for development entry.

When the product being detailed is **not the runtime itself**, but a business/control plane built on top of a mature runtime or web-console base, the detailed-design packet must explicitly contain **two distinct layers**:

### PRD / prototype demotion rule for this class of design

When a runtime-backed product already has:
- business-solution documents,
- overview technical design,
- and PRD / prototype materials,

treat them with a strict priority order for **detailed design**:
1. **business solution + overview design** define server-side truth, runtime boundaries, and object ownership
2. **verified code / runtime entry points** define what is actually reusable and callable
3. **PRD / prototype** is used only to check page completeness, user-visible flows, and whether major product-facing capabilities were missed

Do **not** directly lift PRD/prototype material such as:
- pseudo backend endpoints
- page-level field dictionaries
- speculative table names
- mock implementation notes
- page interaction wording that pretends to be runtime truth

into the detailed-design server contract unless that information is independently validated by the business solution, overview design, or code.

Safe use of PRD in detailed design:
- use it to verify functional-domain coverage
- use it to discover missing user-visible states or pages that the design has not yet explained
- use it to pressure-test whether the control-plane objects are complete

Unsafe use of PRD in detailed design:
- treating PRD example fields/interfaces as authoritative backend design
- allowing prototype/UI terms to silently redefine runtime objects
- letting page-level implementation hints override the already-approved server-side architecture

Review heuristic:
- if a detailed-design paragraph can no longer answer "which higher-level design or verified runtime fact makes this true?", it is probably PRD leakage and should be rewritten or removed.

### Reviewer-TODO handling rule for architecture/design documents

When the user reviews a design document and leaves inline TODO comments, do not only answer whether the comment is "reasonable". Classify each TODO into one of these buckets before changing the document:
- **runtime truth correction** — the current text conflicts with verified runtime/code behavior
- **layering correction** — the current text collapses Team Panel / Gateway / Runtime boundaries
- **object-model clarification** — two objects or flows are under-specified and need sharper semantics
- **terminology correction** — a name is misleading relative to the runtime mapping
- **business binding correction** — a business object is being incorrectly equated with an execution object or workflow

For each TODO, prefer the smallest fix that restores the correct layer boundary. In particular, be suspicious when:
- browser/WebUI internal endpoints are described as if they were the formal Team Panel -> Gateway contract
- external message-platform group-chat ingress is conflated with browser-native team collaboration
- a business solution object (for example an industry solution) is treated as identical to an execution workflow instead of a blueprint that may bind a collaboration template
- PRD-origin terms are used where the runtime-backed object model should be named instead

### Multi-document detailed-design boundary repair rule

When a runtime-backed subsystem already has a **hub doc plus several child detailed-design docs**, and a reviewer points out that one child doc contains the wrong layer or misleading terminology, do not stop at locally fixing that one file unless the issue is provably isolated.

Run a boundary-repair pass across the sibling packet:
1. identify the **true owner** of the concept
   - business container / UI semantics -> flow doc or interface doc
   - browser-visible northbound API -> front-end/API contract doc
   - shared enum / cursor / event envelope -> shared contract doc
   - runtime ingress / adapter / handle / hydration / reconcile -> gateway/runtime doc
2. patch the mis-layered file first so it stops lying
3. then patch the sibling docs that must now make the ownership explicit, even if they were not the file originally under review
4. end with grep-level verification that the old misleading term or section title no longer survives in formal docs

Typical runtime-backed examples:
- a Gateway doc titled `Group Run` is actually describing **group-conversation runtime ingress** or **route_mode -> runtime entry translation**, not the full browser-native group-chat flow
- a Gateway doc should not be the authority for `POST /api/team/runs` or other Team Panel northbound REST/SSE contracts; it should point to the interface/shared-contract docs instead
- `group conversation` is a **business conversation container**, while `orchestration` is a **TeamRun execution strategy**; sibling docs must not use them as synonyms

Fast review test:
- if a developer could still ask "which doc owns the browser group-chat flow vs which doc owns runtime ingress?" the packet is not yet converged
- if fixing one doc would make another sibling silently become the only remaining owner of a shared contract without saying so, the repair pass is incomplete

1. **Team Panel / control-plane layer**
   - complete functional-domain inventory
   - business objects and ownership boundaries
   - product-facing flows, states, and governance semantics
2. **Gateway / runtime-mapping layer**
   - how each business object maps to a concrete runtime object, file, task, session, job, or SDK entry point
   - which parts are direct reuse vs extension vs net-new build

Minimum coverage rule for this class of detailed design:
- for **each functional domain**, list the required feature points, not just the module name
- for **each business object**, state its control-plane truth, runtime counterpart, and the mapping rule between them
- cover both:
  - **static mapping** — e.g. employee -> profile, connector grant -> credential/env injection, skill grant -> skill/runtime visibility
  - **dynamic mapping** — e.g. private chat -> single-agent run entry, orchestration task -> kanban task + dispatcher path, loop mission -> cron job
- name the real backend/code entry points from source verification, not README-level inference
- classify every mapping as:
  - **existing directly reusable**
  - **reusable with extension**
  - **must be newly built by the product layer**

This rule is especially important when a mature web UI already exists. Do not collapse:
- the web workbench shell
- the product's business objects
- the gateway/runtime adapter
into one blurred module just because the code lives in one repo.

## Developer-facing subsystem docs consolidation rule

When a user says a subsystem-local docs directory (for example `app/docs/`) is **too scattered** and wants it reorganized for developers, do not keep accreting more one-off files beside old notes. Treat this as a small documentation-architecture task with explicit audience separation.

Preferred restructuring pattern:
1. define the local docs directory as **usage-layer / developer-operation docs only**
2. move design-process material (RFCs, design drafts, architecture exploration notes) out to a repo-level design-notes area
3. rebuild the local docs set around the developer's actual questions, using plain-language names rather than process jargon
4. update nearby references, comments, tests, and ignore rules so the old paths do not silently rot
5. delete the superseded local docs once the replacement set exists

Recommended shape for a runtime-backed product/app subtree:
- `README.md` — doc index / who should read what first
- `先看这个.md` (or equally plain language) — subsystem role, boundaries, current reality, what is and is not implemented
- `启动、联调和跑测试.md` — startup, local dependencies, test order, container-vs-local guidance
- `MVP验收和排障.md` — acceptance paths, page/API verification, logs, troubleshooting flow

Important boundary rule:
- top-level `docs/` design packets remain the authority for architecture, contracts, schemas, and process rationale
- subtree docs such as `app/docs/` should explain **how to use, run, verify, and debug the subsystem as it exists now**
- if RFCs are still valuable, keep them, but relocate them under a repo-level `design-notes/` or equivalent path rather than mixing them with onboarding/runbook docs

Verification checklist for this refactor:
- local docs directory now answers "what is this", "how do I start it", "how do I test it", "how do I accept/debug it"
- design/RFC material is no longer mixed with usage docs
- deleted-file replacements exist before removal
- nearby code comments/tests/docs that referenced old paths are updated
- the new file names are understandable to developers without architecture context

## Canonical document placement rule

When writing design documents inside an existing project/vault/repo, land the artifact in the project's **canonical design path first**. Do not draft into ad-hoc temp locations and report completion there unless the user explicitly asked for a scratch copy.

Default behavior:
- detect the project's real design/document directory first
- write the formal artifact to that canonical path
- if permission or tooling constraints force a temporary draft elsewhere, copy it into the canonical destination before claiming completion
- when both a scratch copy and canonical file exist, clearly state which one is authoritative

This prevents a common architect failure mode: doing the thinking correctly but leaving the team looking at the wrong file.

See also `references/runtime-backed-product-mapping.md` for a concrete pattern covering business-object -> runtime-object mapping on top of an existing runtime/web-console base.

## Shared contract freeze rule for multi-document detailed design

When a subsystem already has a **hub detailed-design document + multiple child detailed-design documents**, and review finds that the blockers are no longer about big-picture architecture but about **cross-document contract drift**, do not keep trying to fix each child document independently in parallel.

Default move:
1. identify the drifting shared-contract set explicitly — typically one or more of:
   - timeline / event protocol
   - cursor / replay contract
   - persisted lifecycle enums vs UI projection states
   - northbound API request/response shapes
   - role / authorization model
2. create a short, separate **shared contract freeze document** under the canonical design path
3. make that freeze document the **single adjudication layer** for those shared concerns
4. **update each child document to remove the old conflicting definitions, not just add a reference to the freeze doc**
   - grep for old event names / cursor formats / state enums across all formal child docs
   - delete or rewrite sections that define the same contract locally
   - add an explicit inheritance statement pointing to the freeze document
5. run grep-level verification to confirm no old terminology remains in formal child docs (excluding archive/定稿版)
6. only after grep-clean and freeze-doc reference are both confirmed, continue tightening module-local details

**Critical pitfall**: creating the freeze document does NOT automatically clean child docs. Reviewer cards will still flag "定稿版已建立唯一裁决口径，但主稿/流程稿/Gateway稿/接口稿仍保留旧状态机、旧cursor/事件协议" unless you explicitly grep-and-replace the old definitions out of each formal child doc.

Recommended artifact naming in Chinese project docs:
- prefer names like `共享运行口径定稿版`, `共享契约定稿版`, or `开发口径定稿版`
- avoid naming that sounds like a work log or review memo
- consistently use `定稿版` rather than `冻结版` to avoid hard-calque English translation feel

## Cross-document consistency verification rule

When a subsystem has multiple detailed-design documents and a shared contract freeze/final draft document has been created, do not rely on manual reading alone to verify that child documents are consistent.

Use grep-level verification:
- after updating the freeze document, grep for old event names / cursor formats / state enums across all child docs
- check that no child doc still references deprecated terminology (for example `event: run_created` vs `event: timeline`, or `event_cursor: "evt_000128"` vs `cursor_no: bigint`)
- verify that field names, enum values, and payload shapes match the freeze document's definitions

Typical grep targets after a freeze pass:
- event type names (old raw names vs new unified timeline envelope)
- cursor format patterns (string vs numeric)
- state/status enum values that may have drifted across docs
- role/permission vocabulary that may be inconsistent
- field names in northbound API examples

**Concrete grep pattern for terminal verification**:
```bash
# Check for old event names still in formal docs (excluding archive/定稿版)
grep -r "event: run_created" --include="*.md" <design_dir> | grep -v "archive" | grep -v "定稿版"

# Check for old cursor format (string pattern vs numeric)
grep -r "event_cursor.*evt_" --include="*.md" <design_dir> | grep -v "archive" | grep -v "定稿版"

# Check for old state enum (example: conversation.status values)
grep -r "conversation.status.*active.*muted.*archived" --include="*.md" <design_dir> | grep -v "archive" | grep -v "定稿版"
```

Run each grep before claiming "document set is converged". If any match appears in formal child docs, the convergence is incomplete.

This prevents the common failure mode where the freeze doc looks correct but child docs silently contradict it with legacy terminology left over from earlier drafts.

Why this works:
- it catches terminology drift that manual review misses
- it turns a `block` finding into a bounded grep-and-replace task
- it gives the reviewer confidence that the document set is truly self-consistent before unblocking development entry
- canonical event/timeline envelope and enum list
- canonical external cursor format
- canonical persisted-state vs projection-state split
- canonical northbound API examples
- canonical role/permission vocabulary
- precedence rule: if child docs conflict, the freeze doc wins

Why this works:
- it prevents frontend/backend/data/ops from each reading a different child doc and re-inventing the same contract
- it preserves module docs for implementation detail while giving the team one place to settle shared language
- it turns a `block` finding into a bounded document convergence task instead of an endless round of local edits

## Chinese terminology rule: avoid hard-calque terms like 真相 / 冻结 / 投影 in formal architecture docs

In Chinese architecture/design deliverables, when the real meaning is **single source of truth / authoritative source / canonical contract / freeze the contract**, prefer:
- `口径`
- `权威口径`
- `统一口径`
- `共享契约`
- `定稿`
- `收口`
- `裁决文档`

Avoid using `真相` and `冻结` as default formal document terms in Chinese unless you are explicitly translating external English phrasing.

Use `真相` only when:
- translating or discussing external English phrases directly, or
- deliberately contrasting runtime facts vs control-plane layers in an informal explanation

Use `冻结` only when:
- the business domain literally means freezing / locking / read-only control, or
- you are quoting an upstream English workflow term and have not yet localized it

### Projection/projection-state wording rule for Chinese design docs
When the real meaning is a UI/read-side derived state or assembled read result, do **not** default to hard-calque words like `投影态` / `投影层` / `投影器` in Chinese implementation docs.

Prefer by context:
- UI derived state → `展示态`
- read-side assembled result / page-facing combined result → `聚合视图`
- query-side stable structure → `读模型`
- transformation component → `组装器` / `转换层`

Practical rule:
- if the audience is implementation engineers reading a Chinese detailed-design packet, default to `展示态 / 聚合视图 / 读模型`
- only retain English `Projection` when you are explicitly discussing a CQRS/Event-Sourcing mechanism and the English term is clearer than a forced Chinese translation
- avoid putting `投影` in Chinese document titles unless the team already uses that term as an established in-house convention

Default wording guidance:
- for cross-team shared contracts, say `统一口径` / `权威口径`
- for the final shared-contract artifact, prefer names like `共享运行口径定稿版` / `共享契约定稿版`
- for persisted ownership, say `权威来源` / `归属边界`
- for review findings, say `口径不一致` / `缺少统一裁决文档`, not `没有真相`
- for readiness language, prefer `定稿` / `收口` over `冻结`
- for UI/read-side derived semantics, prefer `展示态` over `投影态`
- for page/BFF combined results, prefer `聚合视图` over `投影视图`

This keeps formal Chinese design docs more natural, easier to review, and closer to common engineering language.

## Diagram Set Selection Rule

Detailed design should not default to **one** big diagram plus prose.

Choose the minimum diagram set that removes implementation ambiguity:
- **技术架构图** for stable runtime structure, control plane vs execution plane, and major storage/query boundaries
- **核心流程图** for the main happy path and key exception branches
- **时序图** when reviewers need actor-by-actor ordering, timing, or lifecycle visibility across components
- **数据流 / 对象关系图** when confusion is really about what objects exist, which layer owns them, and how they move
- **状态机 / 回滚图** when correctness depends on state closure, active-version switching, compensation, or rollback semantics

Rule of thumb:
- if a reviewer could ask “but in what order do these components actually talk?” → add a sequence diagram
- if they could ask “what data/object is flowing here?” → add a data-flow/object relation diagram
- if they could ask “how does version switch / rollback actually close?” → add a state or rollback-focused diagram

Do not wait for the user to explicitly request each companion diagram when the ambiguity is predictable.

## Chinese terminology rule for projection / projection state / read-side view

In Chinese architecture and detailed-design artifacts, do not default to the literal calque **`投影`** for every use of English `projection`, especially in product/control-plane documents read by mixed backend/frontend/product teams.

Use terminology by intent:
- **聚合视图** — when the result is a page-facing or API-facing assembled result built from multiple business objects/events
- **读模型** — when the focus is read-side organization, query-side shape, or a CQRS-style query structure
- **展示态** — when the meaning is a UI-visible derived state computed from persisted main state + latest run/event context
- keep **Projection** in English only when you are explicitly discussing the technical CQRS/Event-Sourcing mechanism itself and Chinese wording would become less clear

Avoid these as default formal wording in Chinese docs unless the audience is already deeply CQRS-native:
- `投影层`
- `投影器`
- `投影视图`
- `投影态`

Preferred rewrite examples:
- `Workbench Projection` -> `工作台聚合视图`
- `Conversation projection state` -> `会话展示态`
- `read-side projection refresh` -> `读模型刷新` or `聚合视图刷新`

Review rule:
- if the document is about implementation handoff for a Chinese-speaking engineering team and the sentence still reads like a direct translation from English, replace `投影` with the more specific Chinese term above.
- if no natural Chinese term fits better, keeping the English `Projection` is preferable to a hard-calque that reduces legibility.

This is especially important for runtime-backed product/control-plane designs, where the intent is usually not mathematical projection but assembled page/API results or UI-derived states.

## Child-module detailed-design companion-diagram rule

When a subsystem-level detailed-design hub document already contains the top-level technical architecture, core flow, release/rollback, and data/object relationship visuals, do **not** mechanically duplicate a “技术架构图” inside every child-module detailed-design note.

Instead, choose child-module diagrams by the specific implementation ambiguity they remove:
- if the module risk is **step order / actor sequencing**, add a **module-level sequence diagram**
- if the module risk is **internal stage boundaries / branch closure**, add a **module-level flowchart or state/closure diagram**
- if the module risk is **object ownership / truth-vs-replica layering**, add a **module-level data-flow or layered object diagram**
- only add a child-module technical-architecture diagram when the module truly has enough internal runtime structure to justify its own stable architecture view

Default anti-pattern to avoid:
- one subsystem hub has a good top-level architecture diagram
- then every child module gets another smaller architecture diagram that repeats the same planes with renamed boxes
- result: visual duplication rises, but implementation ambiguity remains

Preferred child-module diagram choices by common module type:
- **collector / ingress modules**: collection-to-enqueue control flow, checkpoint/state-advance gates, oversize/failure closure
- **pipeline / execution modules**: single-item sequence diagram plus internal stage-flow / branch diagram
- **query / serving modules**: hot-path sequence/flow plus cold-path refresh/control-path diagram
- **storage / replica modules**: layered ownership / truth-store vs serving-store vs replica diagram

Fast review test:
- if the child-module figure still reads mostly like a reduced copy of the subsystem architecture, it is probably the wrong diagram
- if the figure lets an implementer answer “who runs first, where does version freeze, where does failure stop, which store is truth, which path is hot vs cold” in ~30 seconds, it is likely the right child-module companion artifact

## Naming Boundary For Detailed Design Artifacts

When the deliverable is a **system-level detailed design document**, name it as the system/subsystem detailed design artifact itself (for example `XX详细设计方案`) rather than a lower-level working label such as “子模块设计”.

Reserve names like “子模块设计” for true module-collection indexes, not for the canonical subsystem-level detailed design document.

## Common Pitfalls

### Developer-facing repo docs: do not over-split orientation notes
When the deliverable is a **developer-facing documentation packet inside a code repository** (for example `app/docs/` runbooks, onboarding, acceptance, troubleshooting), do not mechanically create an extra orientation note such as `先看这个.md` if the same role can be cleanly absorbed by `README.md`.

Preferred rule:
- keep the packet as small as possible
- let `README.md` serve as both the index and the minimal repo/subsystem orientation note when that is enough
- split into additional docs only when the content is genuinely too large or has a separate maintenance lifecycle

Naming rule for this class of doc:
- for developer-facing operational/runbook files in repos, default to **clear English filenames** (for example `development-workflow.md`, `mvp-acceptance.md`) unless the user explicitly wants localized filenames
- the document body can still be Chinese when that is the team working language

Fast review test:
- if one file exists only to tell the reader to go read two other short files, merge it back into `README.md`
- if filenames are mixed Chinese/English without a clear reason, normalize them before calling the doc set finished

1. **Over-splitting detailed design into too many tiny skills.**
   The detailed stage has many surfaces, but not every surface deserves its own always-live specialist skill.

2. **Reusing overview-level abstractions as if they were sufficient for implementation.**
   Detailed design must be concrete enough to guide execution.

3. **Treating NFR/security/recovery as optional appendices.**
   These are part of the real design, not postscript polish.

4. **Stopping after one general flow diagram.**
   When time-order, object ownership, or rollback semantics are central, one high-level flowchart is not enough.

4. **Delivering only one generic flowchart for a subsystem detailed design.**
   A subsystem-level detailed design often needs more than one visual artifact. Do not wait for the user to explicitly ask for sequence/data-flow/release-flow diagrams if the text still leaves runtime order, control-plane behavior, or object boundaries ambiguous.

5. **Naming the subsystem detailed-design master note too generically.**
   For vault/document delivery, prefer the canonical artifact name `《<子系统名>详细设计方案》` for the subsystem-level detailed-design master document. Do not publish the main detailed-design document under a generic working name such as `子模块设计` unless the user explicitly asked for that as a folder/index, because it confuses the artifact's level and role.

6. **Reviewing design documents without cross-referencing source code.**
   When a detailed-design document cites an existing codebase as a reference or reuse target, do not review the design doc in isolation. Open the actual source files it references and verify that the claimed capabilities, data structures, and mechanisms actually exist. A design that says "复用 hermes-webui 的 SSE 能力" should be verified against `api/streaming.py` to confirm those capabilities exist and have the claimed shape. This prevents the common failure mode where design docs assume capabilities that the source code does not provide.

7. **Specifying database writes in event-streaming hot paths without addressing throughput.**
   When a detailed design places database INSERT operations in the event-streaming path (e.g., persisting every token event to a SQL database before pushing to SSE), the design must explicitly address: (a) whether writes are synchronous or async, (b) batch size and flush interval, (c) queue backpressure protection, (d) whether persistence blocks real-time delivery. A design that says "写入 Event Store" without these details is incomplete for event-streaming scenarios.

8. **Making technology choices without selection rationale.**
   When a design document states "使用 PostgreSQL" or "采用 X 方案" without explaining why that choice was made over alternatives, flag it. At minimum, the document should address: what alternatives were considered, what factors drove the decision, and what trade-offs were accepted. This applies especially when the choice has performance, cost, or complexity implications.

## Diagram Completeness Rule For Detailed Design

When writing a subsystem-level detailed-design document, proactively decide whether the packet needs companion diagrams beyond the top technical architecture figure. Use this default test:
- if the reader may ask **"运行时先后顺序是什么"** → add a sequence diagram
- if the reader may ask **"控制面对象和运行时对象怎么衔接"** → add a data/object-flow diagram
- if the reader may ask **"发布、切版本、回滚怎么发生"** → add a release/rollback sequence or flow diagram
- if one core flow chart cannot answer all three cleanly, split the visuals instead of overloading one figure

This is a default architect responsibility, not an optional embellishment to wait for user prompting.

## Detailed-design complete → implementation-planning handoff rule

When the subsystem already has:
- solution design,
- overview technical design,
- shared contract / 口径定稿,
- and module-level detailed design,

then the next stage is **not** another round of architecture ideation and **not** direct coding.
The next required artifact is an **implementation planning packet**.

For this class of system, prefer these rules:

### 1. Construction axis: build bottom-up by system layer / capability dependency
Default execution order:
1. shared contracts / host seams
2. data foundation / schemas / repositories / transaction boundary
3. Team Panel or control-plane service layer
4. runtime adapter / gateway layer
5. frontend / BFF expression layer
6. business-flow stitching / end-to-end scenario closure

Why:
- multiple business scenarios reuse the same lower-layer capability set
- feature-first slicing too early causes duplicate foundation work
- parallel teams otherwise invent competing event/state/API contracts

### 2. Validation axis: test each layer before moving upward
For users or teams that explicitly prefer foundation-first delivery, do **not** optimize the plan around getting the first demo flow as early as possible.
Instead:
- each task should carry unit-test-first or verification-first steps
- each layer must have a layer-level integration test gate
- only after a layer passes its integration gate should the next layer become the primary implementation target
- business scenarios are used later for stitching and acceptance, not for redefining lower contracts

### 3. Design-alignment gate before coding
Before turning a detailed design into Kanban-ready execution tasks, run a design-to-plan alignment check:
- authoritative data model fields and enums are fully represented in the plan
- runtime handle / binding fields are not simplified beyond the approved design
- Team Panel / Gateway / Runtime boundaries are not blurred by convenience
- frontend still consumes Team Panel API only
- no second protocol or shadow object model was invented in the plan

If alignment is incomplete, revise the plan before allowing implementation to start.

### 4. Plan package shape for this class of project
Preferred implementation planning packet:
- one layered master plan
- one execution plan per layer
- task IDs and dependency markers suitable for Kanban ingestion
- explicit unit-test commands and layer integration-test commands
- code-shape snippets for the key contracts, entities, adapters, and fixtures that would otherwise force the implementer to guess

### 5. Acceptance-driven development readiness packet (important when the user wants full-PRD delivery without drift)
When the user explicitly wants **开发任务、review、QA、PM 全部围绕同一验收标准执行** — especially after a project already suffered from `acceptance drift`, `story done != PRD done`, or shell/stub closeout inflation — do not stop at:
- one implementation plan, plus
- one PRD-row acceptance matrix.

Add a mandatory middle packet focused on **task-level acceptance truth**:
1. **PRD-row acceptance matrix** — answers `哪些功能行必须交付`.
2. **Functional-domain acceptance spec packs** — answer `每个功能点怎么算做完`.
3. **Task-card templates for dev/review/QA/PM** — answer `每个角色如何引用同一 spec 执行职责`.
4. **Execution-ready checklist** — answers `现在是否已经可以正式开工`.

Recommended organization rule:
- **organize files by functional domain** (for example P05 chat, P06 group chat, B05 connectors)
- **organize execution granularity by feature point/spec item** inside each file
- do **not** create one document per tiny feature point; that explodes maintenance cost
- do **not** keep only coarse domain-level acceptance language; that leaves too much room for implementer drift

Recommended spec-item shape inside each functional-domain acceptance pack:
- `spec_id`
- `covers_prd`
- `feature_point`
- `user_visible_expectation`
- `design_refs`
- `dev_done_when`
- `review_checkpoints`
- `qa_checks`
- `pm_acceptance`
- `not_done_if`
- `evidence_required`
- `parallel_notes`

Recommended execution rule:
- every dev/review/QA/PM card must cite both `covers_prd` and `spec_refs`
- PM may upgrade a PRD row only when its relevant `spec_id` evidence is complete
- if the project has design-conflict rows (`in_design_conflict`, `contract_locked_but_not_complete`), create **decision gates** before implementation cards, not after

### 6. Parallel implementation isolation rule for multi-agent execution planning
When the acceptance packet is meant to drive **multiple coding agents in parallel**, the plan must state the code-isolation policy explicitly. Do not assume a shared dirty working tree is survivable.

Preferred default:
- use **git worktrees by lane / functional-domain cluster**, not by every micro-task and not by a single shared main workspace
- require task cards to declare:
  - `worktree_scope: dedicated | shared-lane | master-forbidden`
  - `worktree_name`
- treat `master-forbidden` as the default for dev/review/QA task execution in multi-agent runs

Heuristic:
- one worktree per **functional-domain lane** is usually the right balance
- dedicated worktree for high-conflict/high-risk changes (shared contracts, root routing, global shells, billing/auth/security)
- shared-lane worktree for several sequential small cards in the same domain
- avoid both extremes:
  - everyone editing one shared workspace
  - one worktree per micro-task when the tasks are really one sequential lane

### 6A. Canonical execution-doc boundary rule for acceptance-driven delivery

Once the acceptance-driven packet is in place, **do not create an additional long-lived canonical layer of batch-specific task-list documents** such as `第一批开发任务清单 / 第二批开发任务清单 / 第三批开发任务清单` unless the user explicitly wants a temporary bootstrap artifact.

Preferred canonical stack:
1. `PRD 行级验收矩阵` — global scope truth
2. `功能域验收规格包` — finest-grained long-lived execution guidance
3. `任务卡模板 / 执行规则` — how cards must be shaped
4. real Kanban cards / board rows — execution instances
5. per-spec appendix / execution ledger — task IDs, kanban IDs, evidence links, current status

Default rule:
- treat the **functional-domain acceptance-spec document** as the finest-grained durable reference for development / review / QA / PM
- create execution cards directly from `spec_id`
- record actual execution back into the spec document's appendix or execution ledger fields
- do **not** maintain a parallel evergreen document family that re-lists wave-1 / wave-2 / wave-3 task splits, because it quickly becomes a second drifting source of truth

Use a temporary batch task list only when:
- the user explicitly asks for a one-off board snapshot, or
- you need a short-lived bootstrap artifact before the board exists

Even then:
- label it as temporary / non-canonical
- retire it once the real Kanban cards exist
- keep the long-term source of truth in the spec packet, not in batch docs

### 6B. PM review gate before claiming implementation-ready

Do not claim `acceptance-driven development ready` only because the matrix exists, the spec packet exists, task templates exist, and worktree rules exist.

For product-facing systems locked to PRD / prototype expression, require an explicit **PM review gate** over the acceptance-driven packet before declaring true execution readiness.

The PM gate should verify at minimum:
- the spec packet does not merely cover module names, but actually covers the PRD's feature points and interaction intent
- high-expression pages are not weakened into generic dashboard / CRUD / shell acceptance language
- `mock-first` means only the external provider may be mocked, not the visible product path
- conflict / merged / deferred rows (for example `B03`, `P10`, `B09`, login-provider realism) are written as executable PM gate decisions, not vague future placeholders
- the ready checklist is a real PM release/entry gate, not only a document-existence checklist

If PM review finds unresolved product-semantics blockers, downgrade the state from `implementation-ready` to `planning_ready_but_not_execution_ready` and fix the packet before wide fan-out.  - one worktree per tiny card

Use this rule especially when the repository already has a history of dirty main-worktree delivery or unmerged parallel worktree residue. In that situation, execution readiness is not only about docs; it also requires an explicit isolation strategy in the implementation packet.

## Verification Checklist


## Verification Checklist

- [ ] Detailed design clearly inherits from a stable overview design
- [ ] Runtime, project, flow, data, interface, and NFR surfaces are adequately covered
- [ ] Key protections and recovery assumptions are explicit
- [ ] Separate specialist skills are used only where the split really adds clarity
- [ ] The design is implementation-guiding, not only principle-asserting
- [ ] If detailed design is complete, the next artifact is an implementation-planning packet rather than fresh architecture ideation
- [ ] The implementation plan uses bottom-up layered construction when the system has heavy shared foundations
- [ ] Each layer has an explicit verification / integration gate before upper layers depend on it
- [ ] The plan was checked against authoritative detailed-design fields / enums / boundaries before execution started


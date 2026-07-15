---
name: software-design-lifecycle-tech-detailed-core-flow-diagramming
description: "Use when an architect in detailed technical design must produce or review the 关键流程图 or 状态机 and needs the fully merged branch-complete core-flow specialist method directly inside the new lifecycle family."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, technical-design, detailed-design, core-flow]
    related_skills: [arch-lifecycle-tech-detailed-methodology, arch-lifecycle-tech-detailed-technical-arch-diagramming]
---

# Technical Detailed Core Flow Diagramming

## Overview

This skill now directly contains the merged specialist doctrine that previously lived in the legacy core-functional-flow skill.

## Integrated Legacy Specialist Doctrine (Preserved and Merged)

# Core Functional Flow Diagramming

## Overview

This is the architect-private skill for drawing and reviewing **核心功能流程图** in the **详细设计** stage.

Its job is narrow in lifecycle position but intentionally complete in method.
It combines:
- the **architecture-lifecycle doctrine** for `技术方案设计 → 详细设计 → 关键流程`
- the **branch-completeness discipline** needed for design review
- the **full drawing specification**, preserved in linked references instead of being compressed away
- the **ready-to-use templates** needed to turn the method into an actual deliverable

Core principle:
**核心功能流程图 belongs to detailed design. It explains dynamic handling logic and branch completeness, not overview capability layering, business-solution framing, deployment topology, or schema/interface inventories.**

## Bundle Map

### References
Load these when doing actual diagram work. They preserve the detailed drawing spec and should be treated as part of this skill, not optional extras.

- `references/diagram-grammar.md`
  - full node/edge grammar
  - abstraction levels
  - decoupling from product names
  - color / typography rules
  - detailed-design branch/ownership rules
  - anti-patterns
- `references/layout-cookbook.md`
  - layout patterns A-F
  - linear flow / layered stack / hub-and-spoke / event mesh / closed loop / Saga compensation
  - pattern selection guidance
  - split-into-multiple-diagrams rules
- `references/tool-recipes.md`
  - Excalidraw / Draw.io / PlantUML / SVG / Structurizr guidance
  - end-to-end execution workflow for producing a diagram artifact

### Templates
Use these when you need to actually produce or review a diagram package.

- `templates/drawio-examples.md`
  - starter draw.io patterns
  - flowchart / event-driven / Saga / state-machine examples
- `templates/excalidraw-review-checklist.md`
  - handoff-quality review checklist for Excalidraw artifacts
- `templates/review-packet.md`
  - reviewer-facing packet to accompany the diagram

Important rule:
**If the user asked to keep the original drawing detail, do not answer from this main file alone. Load the linked references and preserve the concrete method.**

## Which File To Load For Which Task

- User asks **how to draw the diagram correctly** → load `references/diagram-grammar.md`
- User asks **which layout to choose** → load `references/layout-cookbook.md`
- User asks **how to render in Excalidraw / Draw.io** → load `references/tool-recipes.md`
- User asks **for a Draw.io deliverable** → load `templates/drawio-examples.md`
- User asks **to review an Excalidraw diagram before handoff** → load `templates/excalidraw-review-checklist.md`
- User asks **for a review-ready package** → load `templates/review-packet.md`
- User asks **for the full method or a real delivery workflow** → load all three references, then the relevant template(s)

## Lifecycle Placement

Use this skill in the **技术方案设计 → 详细设计 → 关键流程** part of the architecture lifecycle.

The source doctrine behind this skill requires the detailed-design key-flow artifact to answer:
- **有哪些关键业务流程？通过什么图表来体现？**
- **不同角色不同阶段的处理过程，以及各种分支情况的条件及处理逻辑，要覆盖所有流程分支，不重不漏。**

This artifact therefore sits beside, not instead of:
- 技术架构
- 项目架构
- 数据结构
- 数据架构
- 系统接口
- 非功能性需求

Important boundary:
- this skill owns **关键流程表达**
- it does **not** swallow technical stack topology, project/package structure, API contract inventories, storage schema, or deployment topology

## When to Use

Use when:
- the user asks for 核心功能流程图、关键流程图、详细设计流程图、状态机补充图
- the difficult question is not “what functions exist” but “how one key flow actually runs, branches, fails, retries, or ends”
- different actors or stages materially change handling logic
- the system has non-trivial exception paths, rollback logic, pending ownership, or state closure requirements
- a design review must verify that no major branch has been omitted
- a technical design doc needs a companion artifact beside technical architecture, project architecture, interface design, and data design
- the user expects full drawing guidance rather than only lifecycle framing

Do not use when:
- the dominant question is business problem framing or end-to-end solution shape before technical design → use `arch-lifecycle-solution-design-biz-arch-diagramming`
- the dominant question is capability layering or overview functional decomposition → use `arch-lifecycle-tech-overview-functional-arch-diagramming`
- the dominant question is subsystem boundaries and collaboration topology → use system architecture / technical architecture artifacts
- the dominant question is table/index/storage structure → use data architecture / ER artifacts
- the dominant question is API contract definition, idempotency fields, result codes, auth parameters, or timeout catalog → use interface design artifacts
- the dominant question is deployment nodes, networks, processes, zones, replicas, or machine placement → use deployment / physical architecture artifacts

## What This Diagram Must Answer

A qualified core-functional-flow artifact should let a reviewer answer these quickly:
1. what key flow is being described
2. what event, role, timer, or upstream action triggers it
3. which roles participate and where ownership changes hands
4. what stages the flow passes through
5. what the main path is
6. what the meaningful branches are
7. what condition triggers each branch
8. what handling logic each branch follows
9. what terminal states exist
10. whether exception, rollback, retry, cancellation, or pending paths are accounted for
11. whether the flow is complete **without double-counting or omission** at the chosen abstraction level

If the diagram cannot answer those questions, it is not finished.

## Artifact Boundary With Neighboring Diagrams

Keep this artifact cleanly separated from adjacent architecture views.

### Versus business-solution architecture
- **Business-solution architecture** explains the operational solution shape from business action to business result.
- **Core functional flow** explains one critical run path in detailed-design terms.

### Versus system functional architecture
- **Functional architecture** is stable capability structure and layering.
- **Core functional flow** is dynamic behavior, handoff, branching, and completion logic.

### Versus technical architecture
- **Technical architecture** explains stacks, engines, middleware, processes, tasks, and runtime carriers.
- **Core functional flow** explains the logic of the critical path. It may mention abstract services or modules, but it should not become a stack diagram.

### Versus project architecture
- **Project architecture** explains code/module/component/dependency organization.
- **Core functional flow** explains runtime handling logic at a level above code structure.

### Versus data / interface design
- **Data structure / data architecture** explain what data exists and how it is stored.
- **Interface design** explains request/response contracts, idempotency, errors, retries, auth, limits.
- **Core functional flow** only references these when they materially alter branch behavior.

Important review rule:
**If the current “流程图” mainly communicates middleware topology, package structure, API field catalogs, or storage schema, it is the wrong artifact.**

## Flowchart vs State Machine

The lifecycle source explicitly permits both **流程图** and **状态机**. Choose deliberately.

### Use a flowchart when
- the hard part is role/stage progression
- the reader must understand processing order and branch routing
- handoffs, approvals, orchestration, or multi-step handling dominate
- external triggers or parallel handling paths matter more than state semantics

### Use a state machine when
- the hard part is status advancement and closure
- terminal states, forbidden transitions, retries, or rollbacks dominate
- one business object’s lifecycle is the real source of complexity
- the main design risk is inconsistent or unowned state

### Pair both when
- the user needs to see both handling order and lifecycle closure
- the main flow is understandable as steps, but correctness depends on status transitions
- a single picture would become unreadable if forced to express both equally well

Default rule:
**If role/stage handling dominates, start with a flowchart. If state closure dominates, start with a state machine. Pair them when one would hide the real difficulty.**

## Delivery Workflow

1. **Frame the key flow**
   - write: `本图回答的关键流程是：____`
2. **Choose diagram type**
   - flowchart / state machine / paired artifacts
3. **Load the right reference(s)**
   - grammar / layout / tools as needed
4. **Choose whether a template is needed**
   - Draw.io example / Excalidraw checklist / review packet
5. **Draw only review-relevant logic**
   - main path
   - branch conditions
   - exception / rollback / pending ownership when relevant
6. **Package for handoff**
   - diagram
   - scope statement
   - why-this-diagram-type note
   - review packet when the review audience is explicit

## Review Gates

### Gate 1: Diagram-type gate
Reject or relabel the artifact if it is really:
- a business-solution architecture picture
- an overview functional architecture picture
- a technical stack topology picture
- an API contract sheet
- a storage/model diagram
- a deployment topology diagram

### Gate 2: Branch-coverage gate
The artifact is weak if it cannot show:
- main path
- exception path
- meaningful branch conditions
- cancellation / rollback / compensation path when relevant
- explicit pending ownership when a path may remain unresolved

### Gate 3: Role/stage gate
The artifact is weak if different roles or stages materially change handling but the picture flattens them into one undifferentiated chain.

### Gate 4: Terminal-state gate
The artifact is weak if it cannot answer:
- what counts as success
- what counts as controlled failure
- what rollback or compensation completes
- who owns unresolved or deferred work

### Gate 5: Abstraction gate
Reject the artifact if it drops into:
- field-by-field payload detail
- result-code inventories
- package/class/function-level decomposition
- machine/process/pod/carrier minutiae
- table/index design

### Gate 6: Legibility gate
Reject the artifact if completeness was technically attempted but the result is unreadable. Split it instead.

## Verification Checklist

- [ ] This is explicitly a **详细设计关键流程** artifact.
- [ ] The diagram names the specific key flow it answers.
- [ ] The trigger source is clear.
- [ ] All materially different roles are represented where they affect handling.
- [ ] All materially different stages are represented where they affect handling.
- [ ] The main path is identifiable quickly.
- [ ] Every meaningful branch has an explicit condition or trigger.
- [ ] Normal path is covered.
- [ ] Exception path is covered.
- [ ] Rollback / compensation / cancellation path is covered when relevant.
- [ ] Pending paths, if any, show explicit ownership.
- [ ] Terminal states are clear.
- [ ] The flow is complete at the chosen abstraction level, without obvious omission or double-counting.
- [ ] Flowchart vs state-machine choice is justified.
- [ ] The artifact did not drift into technical stack, deployment, schema, or interface detail.
- [ ] Product-specific tech names are absent from the core flow unless strictly necessary in captions/notes.
- [ ] If the picture became too dense, it was split into honest companion artifacts.
- [ ] The linked references were consulted when concrete drawing details were needed.
- [ ] The right template was loaded when a concrete deliverable format was required.

## Relationship to Other Architect Skills

Use this skill alongside, not instead of:
- `arch-lifecycle-solution-design-biz-arch-diagramming` for pre-technical solution framing
- `arch-lifecycle-tech-overview-functional-arch-diagramming` for overview functional layering
- `arch-lifecycle-delivery` for the end-to-end staged architecture package

Think of the split like this:
- **business-solution architecture** answers: what operational solution shape solves the problem?
- **functional architecture** answers: what capabilities exist and how are they layered?
- **core functional flow** answers: how does one critical path actually run, branch, fail, and end?

That boundary discipline is the main reason this skill exists.


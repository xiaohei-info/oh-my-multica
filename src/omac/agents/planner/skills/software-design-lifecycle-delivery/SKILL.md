---
name: software-design-lifecycle-delivery
description: "Use when an architect must decide which architecture lifecycle stage to enter next, route to the correct stage methodology skill, and apply the merged lifecycle backbone as one coherent delivery chain rather than as separate old and new skill systems."
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, lifecycle, routing, delivery, stage-gates]
    related_skills: [arch-lifecycle-demand-survey-methodology, arch-lifecycle-solution-design-methodology, arch-lifecycle-tech-overview-methodology, arch-lifecycle-tech-detailed-methodology, arch-lifecycle-deploy-ops-methodology, ddd-domain-modeling-for-architecture, design-patterns-and-refactoring]
---

# Architect Lifecycle Delivery

## Overview

This is the `architect` profile's **lifecycle backbone and routing skill**.

It now belongs entirely to the new `arch-lifecycle-*` system.
Its role is not to duplicate each stage methodology skill, but to:
- decide the correct lifecycle entry point
- keep stage boundaries clear
- preserve the integrated delivery-chain doctrine that gives the whole family coherence

Core principle:
**architecture is a staged reduction of uncertainty, not one giant diagram and not a late cleanup after coding starts.**

## When to Use

Use when:
- the user asks for 架构方案、设计方案、概要设计、详细设计、技术方案、设计交付包, but the current stage is not yet explicit
- the architect must take work from 0 or from a midstream stage and choose the correct entry point
- the team is mixing business framing, solution design, overview design, detailed design, and deploy/ops handoff responsibilities together
- the architect needs one top-level delivery chain to coordinate survey, diagrams, risk, and handoff

Do not use when:
- the current stage is already explicit and the correct methodology skill can be loaded directly
- the main question is a cross-stage specialist judgment such as DDD boundary modeling or code-structure refactoring rather than lifecycle routing

## Lifecycle Family Map

Primary methodology skills:
- `arch-lifecycle-demand-survey-methodology`
- `arch-lifecycle-solution-design-methodology`
- `arch-lifecycle-tech-overview-methodology`
- `arch-lifecycle-tech-detailed-methodology`
- `arch-lifecycle-deploy-ops-methodology`

Recurring companion skills worth keeping separate:
- `arch-lifecycle-solution-design-biz-arch-diagramming`
- `arch-lifecycle-tech-overview-functional-arch-diagramming`
- `arch-lifecycle-tech-overview-system-arch-diagramming`
- `arch-lifecycle-tech-detailed-technical-arch-diagramming`
- `arch-lifecycle-tech-detailed-core-flow-diagramming`
- `arch-lifecycle-deploy-ops-deployment-arch-diagramming`

Cross-stage specialists that stay outside the main lifecycle chain:
- `ddd-domain-modeling-for-architecture`
- `design-patterns-and-refactoring`

## Stage Routing Rules

- from vague需求 → `arch-lifecycle-demand-survey-methodology`
- from confirmed调研 → `arch-lifecycle-solution-design-methodology`
- from confirmed解决方案 → `arch-lifecycle-tech-overview-methodology`
- from confirmed概要设计 → `arch-lifecycle-tech-detailed-methodology`
- from confirmed详细设计 → `arch-lifecycle-deploy-ops-methodology`

## Integrated Delivery Backbone

### 1. Frame the problem before drawing architecture
First answer:
- who is asking
- what current problem exists
- what target result is expected
- what success metric or ROI matters
- what is out of scope

Minimum output:
- current state
- desired state
- key constraints
- main trade-off axis

If you cannot state the problem in result terms, do not proceed to architecture diagrams yet.

### 2. Survey mature solutions before inventing one
Inspect, at minimum:
- similar open-source systems
- mature product patterns
- industry-standard architecture practices

Look beyond README-level marketing. Inspect:
- capabilities
- interfaces / configuration surfaces
- boundaries and constraints
- runtime requirements
- extension points
- known limitations

For each serious candidate, classify the path:
- direct adoption
- adaptation / secondary development
- interface-level borrowing
- deliberate rejection

Minimum output:
- surveyed references
- reuse judgment per reference
- why the recommended path wins

### 3. Produce the solution-design layer
This is the business / mid-layer design, not the code-level design.

It should answer:
- what business flow is being changed
- what roles / actors exist
- what domain objects or major conceptual boundaries exist
- what the end-to-end solution shape is
- what milestones, rollout constraints, and first-order risks matter

Typical artifacts:
- business flow diagram
- domain boundary sketch
- solution architecture diagram
- milestone / rollout outline
- first-order risk list

Rule:
solution design explains **how the problem will be solved operationally**, not yet how every component is implemented.

### 4. Produce the technical-design layer
This is where solution intent becomes implementable architecture.

At minimum, cover:
- overview / context
- terminology
- design goals
- alternatives and evaluation
- system decomposition
- technical architecture
- key flows / states / sequences
- data structures and persistence
- external interfaces
- non-functional requirements
- known risks

For non-trivial systems, include 1-2 clearly different alternatives.
Evaluate them across weighted criteria such as:
- performance
- reliability
- complexity
- operability
- delivery speed
- cost
- extensibility

Do not present a single path as inevitable unless the constraints truly collapse the search space.

### 5. Produce deployment / operations handoff
Architecture is not finished when the design doc stops.

At minimum, hand off:
- physical topology / deployment shape
- network and dependency assumptions
- deployment SOP
- operations SOP
- monitoring / alerting expectations
- backup / recovery expectations
- rollback / failure-path handling
- ownership and escalation path

If the design has no run-stage handoff, it is still incomplete.

## Diagram Responsibility Map

Use different diagrams for different questions.

- Business flow: which actors, steps, and handoffs make up the end-to-end path?
- Domain model / boundary sketch: what major conceptual objects and boundaries matter?
- Solution architecture: what are the major solution blocks and their relationships?
- Functional architecture: how are capabilities layered?
- System architecture: what subsystems, dependencies, and communications exist?
- Technical architecture: which concrete stacks, engines, middleware, and execution paths realize the system?
- State / sequence / flow: how does one key run branch, retry, fail, and end?
- ER / data model: what persistent structures and relationships must exist?
- Physical / deployment view: where does the system run and how is it connected?

## Boundary and Review Rules

- Different diagrams answer different questions and should not be collapsed into one picture.
- If one diagram only repeats another, merge or drop it.
- If one unresolved question requires a different concern boundary, add the missing view.
- If a stakeholder disagreement is really about runtime, data, ownership, or deployment shape, do not hide it inside a generic overall-architecture picture.
- Risk should change structure choices early, not appear only in QA or operations appendices.

## Chinese terminology normalization for architecture deliverables

When writing or revising Chinese architecture / design documents, do not casually translate `single source of truth` or layered authority concepts as `真相` unless the user explicitly wants that wording.

Preferred wording in Chinese deliverables:
- `共享口径`
- `统一口径`
- `权威口径`
- `唯一口径`
- when the emphasis is data ownership rather than wording consistency, `单一事实来源` is acceptable

Default replacements:
- `数据真相` -> `数据口径` or `数据权威口径`
- `接口真相` -> `接口口径`
- `运行真相` -> `运行口径`
- `流程真相` -> `流程口径`
- `真相源` -> `权威口径源` or `单一事实来源`

Use `真相` only when:
- the audience already uses that term consistently, or
- you are explaining an English source phrase rather than naming a formal Chinese artifact

Review rule:
- if a Chinese design doc uses `真相` heavily as a structural term, run a terminology pass and normalize it before calling the artifact polished
- keep one term family consistent across the whole packet; do not mix `真相 / 口径 / source of truth` randomly across sibling documents

## Common Pitfalls

1. Using this skill as a substitute for the stage methodology skills.
   It is the backbone, not the full per-stage doctrine source.

2. Jumping from vague demand directly into coding or detailed design.

3. Letting one architecture artifact swallow all neighboring artifact responsibilities.

## Verification Checklist

- [ ] The correct lifecycle stage is explicitly identified
- [ ] The matching methodology skill is selected
- [ ] Artifact selection happens after stage selection, not before
- [ ] The delivery chain still includes survey, design, risk, and handoff rather than stopping midstream


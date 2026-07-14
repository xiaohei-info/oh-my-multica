---
name: software-design-ddd-domain-modeling
description: "Use when architecture work hinges on DDD-style domain modeling: subdomains, bounded contexts, context relationships, aggregate boundaries, and how those decisions shape overview or detailed design artifacts."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architect, ddd, domain-modeling, bounded-context, aggregates, overview-design, detailed-design]
    related_skills: [architecture-lifecycle-delivery, solution-survey-protocol, software-design-philosophy, writing-skills]
---

# DDD Domain Modeling for Architecture

## Overview

This skill is the architect-private **domain modeling workflow** for architecture design.

Use it when the real difficulty is not selecting middleware, but deciding:
- how the business should be split into domains and subdomains
- where bounded contexts should begin and end
- how contexts relate and where translation / anti-corruption is needed
- where aggregate and consistency boundaries should sit
- how those modeling decisions should appear in 概要设计 or 详细设计 rather than staying as abstract DDD vocabulary

Core principle:
**domain modeling is not a theory appendix; it is a design tool for reducing semantic and consistency ambiguity before implementation.**

This skill is intentionally narrower than the architect general playbook. It does not try to own the whole architecture delivery chain. It specializes in the part of architecture work where domain boundaries and model structure dominate the design outcome.

## When to Use

Use when:
- the architecture task is at 概要设计 or 详细设计 stage and domain boundaries are still unclear
- the user asks about 领域 / 子域 / 限界上下文 / 上下文映射 / 聚合 / 事件风暴
- the system has business complexity that cannot be explained well by component diagrams alone
- service boundaries or ownership boundaries depend on language/model boundaries
- multiple teams or subsystems disagree on concepts, state ownership, or consistency responsibility

Do not use when:
- the main problem is only delivery packaging or stage planning
- the task is only code-level refactor or implementation optimization
- the problem is mostly diagram set choice with no meaningful domain ambiguity
- the work is a tiny technical feature with no real business boundary question

## Why This Skill Exists Separately

The architect general playbook answers:
- what stages the design should go through
- what artifacts should be produced
- where risk should be front-loaded
- how handoff happens

This skill answers a different question:
- **how to shape the semantic and consistency structure of the system before those artifacts are finalized**

Keep the boundary clear:
- `architecture-lifecycle-delivery` = top-level architecture delivery chain
- `ddd-domain-modeling-for-architecture` = DDD/domain-modeling method used inside architecture design stages

## Macro / Micro / Overall Capability Lens

When reading architect capability material, classify observations into three levels.

### Macro
Questions:
- what are the external system boundaries?
- what are the internal business/module boundaries?
- what mature external practices are worth borrowing?
- what direction should the system evolve toward?

This is where architecture judgment and long-horizon system shape live.

### Micro
Questions:
- what component principles, implementation patterns, and code-level structures still matter?
- what underlying techniques must the architect understand well enough to judge quality?

This includes patterns, refactoring habits, component usage, and engineering literacy.

Important rule:
**micro literacy supports architect judgment, but should not replace domain-boundary work.**

### Overall
Questions:
- can the architect connect business, project, code, and cross-team influence?
- can they make risk, priority, and design intent explicit?
- can they leave behind artifacts that implementation teams can execute?

This is where architect role maturity appears.

## Domain-Modeling Sequence

When deriving architecture from a business problem or architecture corpus, use this order.

1. Define the business problem and result boundary.
2. Identify major domains / subdomains.
3. Identify bounded contexts and their language boundaries.
4. Map context relationships and integration direction.
5. Decide aggregate / consistency boundaries.
6. Decide repository / domain service / application service boundaries.
7. Map the model into overview or detailed design artifacts.
8. Only then discuss detailed implementation shape.

If you start from framework choice or service count before the above, the design is likely premature.

## What to Model Explicitly

### 1. Domains and subdomains

Ask:
- what part of the business is core?
- what is supporting?
- what is generic or commodity?
- what business capability is stable versus frequently changing?

Do not split by org chart alone.

### 2. Bounded contexts

A bounded context is warranted when:
- the same word means different things in different parts of the system
- state ownership must be separated
- consistency rules differ materially
- the collaboration model or integration direction differs
- one part of the model must evolve faster than another

Do not create bounded contexts just to mirror every microservice idea.

### 3. Context relationships

For each context pair, make explicit:
- upstream vs downstream direction
- translation need
- shared language vs translated language
- whether anti-corruption is required
- what synchronization / integration mode exists

If the relationship is fuzzy, later service or API design will also be fuzzy.

### 4. Aggregate and consistency boundaries

Use aggregates when you must protect invariants and transactional meaning.

Ask:
- what must remain internally consistent at command time?
- what can tolerate eventual consistency?
- what state transitions are irreversible or high risk?
- where would a single command crossing too many objects become dangerous?

Do not make aggregates so large that they become pseudo-modules.
Do not make them so tiny that invariants leak into orchestration code.

### 5. Event storming and model discovery

Use event storming or event-first reasoning when:
- business flow is complex
- multiple roles participate in the same process
- state transitions are the real difficulty
- a static object list hides the dynamic business truth

Extract from events:
- commands
- domain events
- policies
- actor responsibilities
- handoff boundaries
- invariant points

## How This Appears in Architecture Design Stages

### In 概要设计

Use this skill to leave:
- domain/subdomain framing
- bounded-context sketch
- major context relationships
- high-level consistency boundaries
- why the chosen service/system split follows business semantics

### In 详细设计

Use this skill to leave:
- aggregate boundary decisions
- command/event/state transition logic
- repository / domain service / application service responsibility split
- translation / anti-corruption placement
- where consistency is strong vs eventual

Important rule:
DDD content should not remain a theory appendix. It should shape diagrams, interfaces, state rules, and service boundaries.

## Diagram Duty for Domain Modeling

Every modeling artifact must answer a dominant question.

- Domain / subdomain map: what major business capability areas exist?
- Bounded-context map: where do language and responsibility boundaries sit?
- Context relationship map: how do contexts collaborate and translate?
- Aggregate sketch: what invariants and consistency boundaries exist?
- Event-flow / state-flow view: how do commands, events, and state transitions interact?

If a diagram cannot be tied to one of those questions, it is likely decorative.

## Specialization Control Rule

When a corpus contains a heavy specialized branch, explicitly decide whether it should:
- shape the general domain-modeling method
- remain a sibling specialist method
- be deferred to a later dedicated pass

Default rule:
**specialized subdomains should not dominate the general architect modeling skill unless the task is explicitly about that subdomain.**

Corollary:
specialist branches may still be worth ingesting into `_wiki`, while remaining out-of-band for the main reusable modeling method.

## Placement Decision Rule

This skill belongs in architect private space when it encodes:
- heavier abstraction than most profiles need
- opinionated architect judgment about domain boundaries
- design-stage use inside overview/detailed architecture work

Promote only the most portable sub-parts to global/shared engineering skills.

## Failure Gate

Stop and rework the design if any of these are true:
- you cannot say what the system boundary is
- you cannot say what the main domains or subdomains are
- you cannot say why one bounded context should be separate from another
- you cannot identify where strong consistency is required
- the artifacts describe implementation detail but not semantic boundary or responsibility

## Common Pitfalls

1. Treating DDD as vocabulary collection rather than architecture design method.
2. Letting service count drive context boundaries instead of business semantics.
3. Drawing a context map without making translation or dependency direction explicit.
4. Treating every object cluster as an aggregate.
5. Skipping event/stage reasoning when the real difficulty is process and state transition.
6. Reading only the markdown hub note and skipping DDD attachments.
7. Letting a specialized branch define the whole modeling method.
8. Letting micro pattern knowledge replace semantic boundary judgment.

## Verification Checklist

- [ ] The problem/result boundary is explicit
- [ ] Main domains / subdomains are explicit
- [ ] Bounded contexts are stated when relevant
- [ ] Context relationships and integration direction are explicit
- [ ] Aggregate / consistency boundaries are justified
- [ ] The model appears in overview or detailed design artifacts, not only in theory prose
- [ ] Attachment-derived method is distinguished from supporting literacy
- [ ] Specialized branches are either integrated deliberately or explicitly deferred
- [ ] The final method leaves behind reusable modeling judgment, not just DDD terminology


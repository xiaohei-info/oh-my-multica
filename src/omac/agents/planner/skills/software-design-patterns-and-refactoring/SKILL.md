---
name: software-design-patterns-and-refactoring
description: "Use when architecture or backend work needs pre-code structural guidance from design patterns, or post-code cleanup guidance from refactoring and code-smell-driven improvement."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [architecture, backend, design-patterns, refactoring, code-smells, structure]
    related_skills: [ddd-domain-modeling-for-architecture, architecture-lifecycle-delivery, writing-skills]
---

# Design Patterns and Refactoring

## Overview

This skill captures one practical truth: design-pattern thinking is useful before coding, and refactoring thinking is useful after coding — but both can improve the structure of the system when used with restraint.

Its purpose is not to dump a giant catalog of pattern names. Its purpose is to help decide:
- when a structural problem exists before coding
- which pattern family is worth considering
- when code smells indicate refactoring pressure
- how to improve structure without turning the code into abstraction theater

Core principle:
**patterns are design tools, refactoring is structure repair, and both should serve clarity of responsibility rather than decorative cleverness.**

## When to Use

Use when:
- the team is deciding a class/module collaboration structure before coding
- code review reveals repeated structure problems or code smells
- a change is easier to express as a structural pattern decision than as ad hoc branching
- implementation is becoming hard to extend, test, or reason about
- you need one practical bridge between pre-code design and post-code cleanup

Do not use when:
- the problem is fundamentally domain-boundary or system-architecture design
- the task is a trivial code change with no structural pressure
- the user only wants pattern definitions without design judgment
- the code is so small that introducing indirection would only make it worse

## Two Main Uses

### 1. Before-code structural guidance

Use pattern thinking before coding to ask:
- where is variation expected?
- where is behavior currently likely to branch or sprawl?
- where should dependencies be inverted?
- where is composition better than inheritance?
- where should responsibilities be separated instead of packed together?

Typical outputs:
- candidate pattern family
- reason for the pattern choice
- explicit note on what complexity it removes
- explicit note on what extra indirection it introduces

### 2. Post-code refactoring guidance

Use refactoring thinking after coding to ask:
- what smells show the current structure is degrading?
- what responsibility is in the wrong place?
- what duplication, long function, or argument sprawl is accumulating?
- what small structural improvement reduces future change cost?

Typical outputs:
- smell -> action mapping
- minimal refactoring step sequence
- verification plan so cleanup does not silently break behavior

## Pattern Selection Heuristics

Do not select a pattern because it is famous. Select one because it resolves a real pressure.

Good triggers include:
- Strategy-style pressure: behavior families vary but caller workflow is stable
- Observer/event pressure: one state change should notify multiple downstream reactions
- Factory/construction pressure: object creation has branching or environment dependence
- Adapter/anti-corruption pressure: interfaces do not match and direct coupling would spread impurity
- State-style pressure: behavior depends strongly on state and branching is exploding
- Template/decomposition pressure: similar workflows differ only in a few controlled steps

Important rule:
**choose the smallest pattern move that resolves the structural problem.**

## Refactoring Smell Heuristics

Common smell classes to watch for:
- duplicated code
- long functions
- long parameter lists
- mixed responsibilities
- feature envy / wrong object ownership
- scattered conditionals
- state transition logic hidden in many places
- deeply coupled dependency handling

Useful refactoring moves often include:
- Extract Method
- Extract Class
- Pull Up Method
- Introduce Parameter Object
- Preserve Whole Object
- Replace Method with Method Object
- Decompose Conditional

Do not apply a move mechanically. First state what structural problem it solves.

## Using Refactoring Before the Rewrite Is Needed

Refactoring knowledge is not only for after-the-fact cleanup.

Before coding, it can help you avoid predictable future rewrites by noticing:
- where responsibilities are already being mixed in the design
- where a proposed function/module is obviously becoming too broad
- where a data or dependency shape will force duplication later
- where a simpler separation today avoids a bigger cleanup tomorrow

This is the bridge between design-pattern thinking and refactoring thinking.

## Guardrails

- Do not introduce a pattern unless it removes a real source of variation, coupling, or branching pain.
- Do not refactor unrelated code while fixing one localized issue.
- Do not build abstraction layers “just in case”.
- Do not mistake pattern vocabulary for design quality.
- Do not let this skill replace higher-level architecture or domain-boundary work.

## Common Pitfalls

1. Choosing a pattern by name familiarity rather than structural need.
2. Using inheritance where composition would keep change cheaper.
3. Treating every smell as justification for a wide refactor.
4. Refactoring large surfaces without a tight verification plan.
5. Confusing code cleanup with domain or architecture design.
6. Introducing indirection that the codebase cannot justify.

## Verification Checklist

- [ ] The structural problem was named before choosing a pattern or refactor move
- [ ] The chosen pattern/refactor reduces a real complexity source
- [ ] The added abstraction cost is acceptable
- [ ] The change does not replace higher-level architecture/domain work that should happen first
- [ ] Refactoring scope is surgical rather than sprawling
- [ ] There is a verification plan for behavior preservation
- [ ] The outcome leaves the code easier to extend, review, and reason about


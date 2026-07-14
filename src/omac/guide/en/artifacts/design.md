# Design artifact contract

## When to use it

Use this contract during `plan` authoring or review. It makes a design document
a stable basis for acceptance, DAG decomposition, and implementation.

First run:

```bash
omac work show <issue-id> --output json
```

Treat its task, context, authority, guide references, and submit command as
current facts. This static guide adds long-lived format and validation rules; it
does not override instance facts, contract, prior review, or the exact command.

## Minimum valid example

Use Markdown body with YAML front matter:

```md
---
schema: omac.design/v1
title: Login session renewal
problem: A user is signed out without warning when an old session expires.
non_goals:
  - Do not refactor unrelated payment flows.
flows:
  - flow-login-renewal
risk_level: medium
---

# Design document

## Background

An expired session currently returns unauthenticated directly and can lose user input.

## Goals and non-goals

Keep the current operation after successful renewal; fall back to the existing
login flow when renewal fails.

## Business flow

`flow-login-renewal`: request sees an expired session → renew → replay the
original request once.

## Core data

- Session is owned by auth; login creates it, renewal updates it, logout deletes it.
- Empty tokens, duplicate renewal, insufficient permissions, and legacy session
  data have defined outcomes.

## Module boundary

- `auth` owns session state. Business modules call the auth API and do not edit
  sessions directly.

## Cross-module contract

- Input: expired session. Output: renewed session or stable error. Replay at most once.

## Risk and compatibility

- Preserve login, logout, and permission-denied behavior. Renewal failure keeps
  the existing error semantics.

## Acceptance mapping

- `flow-login-renewal` → renewal, fallback, and verification entry points.
```

## Field semantics

Front matter is the structural anchor for machines and later agents; the body
explains implementation intent.

| Field or section | Meaning |
|---|---|
| `schema` | Exactly `omac.design/v1`. |
| `title` | Independently identifiable proposal name. |
| `problem` | The real production problem, not an abstract vision. |
| `non_goals` | Adjacent work explicitly out of scope. |
| `flows` | Stable IDs from the acceptance document. |
| `risk_level` | Change risk; explain source and mitigation in the body. |
| Background, goals, business flow | Why, how far, and how users or systems move through it. |
| Core data | Fields, states, ownership, and create/update/delete paths. Avoid needless copies and conversions. |
| Module boundary | Dependency direction, allowed changes, forbidden changes. |
| Cross-module contract | Call direction, DTOs, events, inputs, outputs, errors, and state changes. |
| Risk and compatibility | Existing behavior, callers, legacy data, and rollback path; do not break userspace. |
| Acceptance mapping | Each flow's design anchor and executable verification entry point. |

Write explicit edge handling for low-reasoning-budget executors: empty input,
duplicates, rollback on failure, permissions, and legacy-data compatibility.
Do not require a named methodology; terminology never replaces data, boundaries,
or contracts.

## Validation gates

1. The current plan submit validator checks only that the file exists and is
   non-empty; that does not prove semantic quality.
2. Front matter includes `schema: omac.design/v1`, problem, non-goals, flows,
   and risk level.
3. Business flows map to acceptance flows; acceptance mapping covers every
   front-matter flow.
4. Core data states ownership and mutation paths; boundaries state dependency direction.
5. Contracts cover input, output, error, and state; risk and compatibility name
   affected existing behavior.
6. Every edge case has a determined outcome rather than a worker guess.

## Project-rules delivery

Every planner-authored `plan` also delivers a separate Markdown file containing
the OMAC-managed project rules for root `AGENTS.md`. It is not a design-document
section and must not include OMAC START/END markers; OMAC owns those markers.

Include only durable repository-wide constraints confirmed by the design, such
as data ownership, dependency direction, compatibility, testing, security, and
stable external contracts. Exclude request goals, implementation steps, issue
IDs, temporary branches, and node-specific instructions. Preserve and do not
contradict existing `AGENTS.md` content.

## Common errors → corrections

| Error | Correction |
|---|---|
| Architecture names without data ownership | Add fields, states, owner, and lifecycle for every core datum. |
| “Decide exception paths during implementation” | Define outcomes for empty input, duplicate requests, rollback, permissions, and legacy data. |
| Boundaries list directories only | Add dependency direction, allowed/forbidden paths, and cross-module interfaces. |
| Flow names drift in prose | Use stable front-matter IDs and map each one to acceptance. |
| Change old behavior for theoretical purity | Name existing behavior and provide a non-breaking compatibility path. |

## Submit

Re-read `work show` and use its exact command. A usual plan shape is:

```bash
omac work submit <issue-id> --plan-file <design.md> --project-rules-file <project-rules.md>
```

Do not submit a verdict during authoring or manually advance platform state.

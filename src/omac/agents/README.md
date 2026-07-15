# Agent Templates

This directory contains the built-in templates that `omac init` can use to
create Multica agents. Each template corresponds to a real, general-purpose
agent profile—not an OMAC lifecycle role. The user still chooses the runtime,
agent name, and role mapping during setup.

## Directory contract

- `_shared/instructions.md` holds engineering rules and the OMAC collaboration
  protocol shared by every template.
- `<template>/instructions.md` defines the role-specific working method,
  boundaries, and output contract.
- `<template>/skills/<skill>/` is a complete skill directory, including its
  `SKILL.md` and every referenced file.
- Templates do not contain nested `AGENTS.md`, `CLAUDE.md`, or `SOUL.md` files.
  Opening this repository in a harness therefore does not load template content
  implicitly. OMAC assembles and injects instructions only when it creates an
  agent.

## What belongs in a template

Each `instructions.md` is the matching profile's role overlay with local
paths, gateway operations, credentials, and runtime-specific dispatch policy
removed. It is the canonical instruction file for both language selections so
the role contract cannot drift between localized copies. OMAC lifecycle roles
such as planner, worker, and acceptor remain workflow assignments; they are
not templates.

They deliberately exclude machine-specific details: absolute paths, profile or
agent instance names, model and provider settings, credentials, personal
workspace conventions, harness launch commands, and tool locations. The OMAC
`work show` / `work submit` protocol stays, because these templates are meant
for OMAC collaboration without tying an agent to a particular runtime.

## Templates and skills

| Template | Bundled skills |
|---|---:|
| `architect` | 40 |
| `backend-eng` | 13 |
| `data-rd` | 0 |
| `frontend-eng` | 13 |
| `orchestrator` | 0 |
| `pm` | 7 |
| `reviewer` | 0 |

Bundled skills are a portable, curated snapshot rather than a copy of a local
profile directory. OMAC reuses a workspace skill with the same name and
uploads only missing skills. It never overwrites an existing agent's
instructions or skill assignments.

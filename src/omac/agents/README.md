# Agent Templates

This directory contains the built-in templates that `omac init` can use to
create Multica agents. Each template corresponds to a real, general-purpose
agent profile—not an OMAC lifecycle role. The user still chooses the runtime,
agent name, and role mapping during setup.

## Directory contract

- `_shared/instructions.md` holds engineering rules and the OMAC collaboration
  protocol shared by every template. `_shared/instructions.en.md` is its full
  English counterpart.
- `<template>/instructions.md` contains the complete Chinese role instructions.
  `<template>/instructions.en.md` is a complete English translation, not a
  fallback or shortened summary.
- `<template>/skills/<skill>/` is a complete skill directory, including its
  `SKILL.md` and every referenced file.
- Templates do not contain nested `AGENTS.md`, `CLAUDE.md`, or `SOUL.md` files.
  Opening this repository in a harness therefore does not load template content
  implicitly. OMAC assembles and injects instructions only when it creates an
  agent.

## What belongs in a template

Each instruction pair follows the matching source profile section by section,
including its complete general rules, risk boundaries, collaboration
preferences, and output discipline. `_shared` adds repository-wide engineering
rules and the OMAC protocol; it does not replace any part of the role file. The
English files preserve the same structure and level of detail as the Chinese
files. They are not compressed rewrites.

Source-only packaging language is removed, including role-overlay labels,
profile migration notes, and instructions to load a particular local runtime
mechanism. OMAC lifecycle roles such as planner, worker, and acceptor remain
workflow assignments; they are not templates.

The templates deliberately exclude machine-specific details: absolute paths,
local service names, gateway commands, model and provider settings,
credentials, personal workspace conventions, harness launch commands, and
local tool locations. Portable role rules remain intact. The OMAC `work show`
/ `work submit` protocol stays because these templates are designed for OMAC
collaboration without tying an agent to a particular runtime.

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

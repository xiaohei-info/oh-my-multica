# Agent Templates

This directory contains the built-in capability templates that `omac init` can
use to create Multica agents. A template defines instructions and skills only.
It does not prescribe an agent name, a runtime, or a final OMAC role; those
choices remain with the user during setup.

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

Templates keep guidance that works across harnesses: good taste, backward
compatibility, practical design, simple code, data-first thinking, TDD,
independent verification, authorization boundaries, and the stable working
methods for planner, orchestrator, worker, reviewer, acceptor, architect,
backend, frontend, and PM roles.

They deliberately exclude machine-specific details: absolute paths, profile or
agent instance names, model and provider settings, credentials, personal
workspace conventions, harness launch commands, and tool locations. The OMAC
`work show` / `work submit` protocol stays, because these templates are meant
for OMAC collaboration without tying an agent to a particular runtime.

## Skill sources

Skill sets follow the current Multica assignments rather than assumptions based
on role names:

| Template | Current source | Skills |
|---|---|---:|
| `architect`, `planner` | `hermes-architect` | 40 |
| `backend` | `hermes-backend-eng` | 13 |
| `frontend` | `hermes-frontend-eng-grok` | 13 |
| `worker` | Shared Codex/Claude engineering-agent set | 13 |
| `pm`, `acceptor` | `hermes-pm` | 7 |
| `orchestrator` | `hermes-orchestrator` | 0 |
| `reviewer` | `hermes-reviewer` | 0 |

Skills are a snapshot taken when the template is created. OMAC reuses a
workspace skill with the same name and uploads only missing skills. It never
overwrites an existing agent's instructions or skill assignments.

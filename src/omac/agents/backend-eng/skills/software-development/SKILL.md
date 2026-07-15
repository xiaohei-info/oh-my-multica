---
name: software-development
description: "Top-level entry point for the software development skill family. Routes to the right sub-skill for design patterns, refactoring, DDD, worktree governance, kanban splitting, container supervision, project governance, runtime ops, and web routing."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [software, development, meta, routing]
    related_skills: [software-design-patterns-and-refactoring, software-ddd-domain-modeling, software-aiteam-worktree-lifecycle-governance]
---

# Software Development (Meta Skill)

This is the top-level entry point for the software development skill family. It does not contain execution guidance itself — it routes to the right sub-skill based on the user's need.

## Sub-Skill Routing

| Need | Sub-Skill |
|------|-----------|
| Pre-code structural guidance (design patterns, refactoring) | `software-design-patterns-and-refactoring` |
| DDD domain modeling (bounded contexts, aggregates) | `software-ddd-domain-modeling` |
| Git worktree lifecycle governance | `software-aiteam-worktree-lifecycle-governance` |
| Kanban task splitting from acceptance specs | `software-acceptance-driven-kanban-splitting` |
| s6-overlay container supervision | `software-hermes-s6-container-supervision` |
| Project direction governance | `software-project-governance-and-repositioning` |
| Runtime-backed app operationalization | `software-runtime-backed-app-operationalization` |
| Web subpath routing and asset base paths | `software-web-subpath-routing` |

## Core Principle

Each sub-skill owns one narrow structural concern. This meta skill only routes — it does not duplicate their guidance.


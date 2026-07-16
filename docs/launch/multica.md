# Multica community launch

Targets checked on July 16, 2026:

- Show and tell: https://github.com/multica-ai/multica/discussions/categories/show-and-tell
- Discord: https://discord.gg/W8gYBn226t

## GitHub Show and tell

### Title

oh-my-multica: production-grade delivery control for Multica Coding Agent teams

### Body

I built oh-my-multica on top of Multica because Multica already solves the hard
runtime problem well: shared workspaces, work items, task queues, connected
Coding Agent runtimes, reusable Skills, and persistent run history.

What I still needed was a software-delivery control layer above that foundation.
For a complex requirement, who turns the goal into reviewed design and
acceptance criteria? Which tasks are actually ready to run in parallel? What
evidence is enough to advance? Who reviews independently? When is merged code
truly accepted, and how does the process continue after an Agent timeout?

oh-my-multica combines two kinds of control:

- Planner and Orchestrator Agents dynamically produce the design, acceptance
  definition, project rules, and manifest DAG for the current repository.
- A deterministic Loop owns dependencies, result collection, evidence gates,
  bounded rework, recovery, merge conditions, and completion.

The early v1 foundation itself was developed through a Multica project: 29 work
items reached done, 168 Agent runs were recorded, and 27 Pull Requests were
linked. Fifteen runs failed and eight were retried. The case study keeps those
failures visible and is explicit about the boundary: the finished controller did
not orchestrate its own entire history.

Repository: https://github.com/xiaohei-info/oh-my-multica

Case study: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/case-studies/building-v1-on-multica.md

Local demo: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/demo/README.md

I would especially value feedback from people already running several Multica
Agents: which delivery boundary is still missing, what repository shape breaks
the current assumptions, and where does the setup ask for too much Human work?

## Discord message

I’ve open-sourced **oh-my-multica**, a production software-delivery control
layer built on Multica.

Multica manages the workspace, work items, runtimes, and execution history.
oh-my-multica adds Agent-authored design and DAG planning, then uses a
deterministic Loop for dependency scheduling, evidence gates, independent
review, bounded rework, merge, recovery, and final acceptance.

There’s a real v1 build record with 29 completed work items, 168 Agent runs, 15
failed runs, 8 retries, and 27 linked PRs — plus a local mock demo that requires
no Multica account or Tokens.

Repo: https://github.com/xiaohei-info/oh-my-multica

I’d love feedback from anyone using Multica for multi-Agent coding work,
especially around setup friction and real repository constraints.

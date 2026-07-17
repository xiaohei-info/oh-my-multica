# Multica community launch

GitHub Show and tell published:
https://github.com/multica-ai/multica/discussions/5545

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

The latest public demo is a Webhook Inbox delivered through a dynamically
planned five-node DAG and five merged Pull Requests. It passed 86 tests with
97.18% coverage. The first final-acceptance round passed only 2/11 flows because
the acceptance source used a stale entry point; the Loop refused completion,
the source was corrected, and the second round passed 11/11 with exit 0.

The early v1 foundation itself was developed through a Multica project: 29 work
items reached done, 168 Agent runs were recorded, and 27 Pull Requests were
linked. Fifteen runs failed and eight were retried. The case study keeps those
failures visible and is explicit about the boundary: the finished controller did
not orchestrate its own entire history.

Repository: https://github.com/xiaohei-info/oh-my-multica

Release: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v1.0.0

Real demo: https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox

Case study: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/case-studies/webhook-inbox-end-to-end.md

Local demo: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/demo/README.md

Install the stable GitHub Tag:

```bash
pipx install git+https://github.com/xiaohei-info/oh-my-multica.git@v1.0.0
```

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

There’s a real Webhook Inbox delivery with a five-node DAG, five merged PRs, 86
tests, 97.18% coverage, and a final acceptance that failed 2/11 before the Loop
forced a correction and reran to 11/11. The repository also includes the early
v1 build record and a local mock demo that requires no Multica account or Tokens.

Repo: https://github.com/xiaohei-info/oh-my-multica

Stable install: `pipx install git+https://github.com/xiaohei-info/oh-my-multica.git@v1.0.0`

I’d love feedback from anyone using Multica for multi-Agent coding work,
especially around setup friction and real repository constraints.

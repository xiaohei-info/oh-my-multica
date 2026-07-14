# OMAC CLI design

[English](omac-cli-design.md) | [简体中文](zh-CN/omac-cli-design.md)

## 1. Purpose

OMAC is a deterministic CLI orchestrator for parallel agent delivery. It turns
a request into a reviewed design, an executable acceptance contract, and a
manifest DAG. The CLI advances that graph until every node is delivered or a
caller must make an explicit decision.

The main design decision is inversion of control: the CLI drives the workflow;
LLMs are finite workers invoked for planning, decomposition, implementation,
review, and acceptance. No agent is asked to supervise an open-ended loop from
chat context.

## 2. Design principles

### 2.1 Never break userspace

- Command paths, JSON keys, exit codes, and state semantics are stable contracts.
- New fields are additive unless a versioned migration says otherwise.
- Existing trading, CI, review, and risk controls must not be bypassed for a
  theoretically cleaner implementation.

### 2.2 Instance facts beat static guidance

For a dispatched task, authority is:

1. `omac work show` current facts.
2. `contract` and `previous_review`.
3. Role guide.
4. Artifact guide.
5. Workflow overview.

Static Guide content cannot override the current task, phase, identity,
deliverable, or submit command.

### 2.3 Evidence before state transitions

Workers submit verification, reviewers reproduce independently, and final
acceptors record one pass/fail result for every acceptance flow. OMAC validates
evidence before advancing platform or manifest state.

### 2.4 Explicit recovery

Failed or blocked nodes do not retry silently. The loop returns exit 20 with a
structured report. The caller chooses `node retry`, `node accept`,
`node abandon`, or a manifest change, then runs the loop again.

### 2.5 Keep platform concerns in engines

Pipeline and CLI code call only `WorkItemStore` and `AgentRuntime`. Multica,
GitHub, and future Linear or Jira commands belong inside engine adapters.

## 3. Callers and content boundaries

| Surface | Primary caller | Responsibility |
|---|---|---|
| Platform issue | Human | Goal, owner, completion criteria, non-goals, and one agent bootstrap command. |
| `omac work show` | Agent | Current task facts, context, authority, Guide references, and exact submit command. |
| `omac work submit` | Agent | Validate and submit one structured deliverable. |
| `omac guide` | Agent | Stable role, artifact, workflow, and recovery knowledge. |
| `omac dag` / `omac node` | Controller | Advance, inspect, and recover the deterministic graph. |
| `omac web` | Human operator | Read-only presentation of command JSON. |

Issue bodies stay human-first. They do not copy long role protocols or submit
argument tables. Agents begin with:

```bash
omac work show <issue-id> --output json
```

## 4. Architecture

```text
CLI / Web
   |
Command functions
   |
Plan pipeline / deterministic DAG loop / evidence gates
   |
WorkItemStore                    AgentRuntime
   |                                 |
Multica adapter / mock adapter   Multica runtime / mock runtime
```

### 4.1 Store plane

`WorkItemStore` owns workspaces, projects, members, issues, metadata,
assignments, statuses, comments, and attachments. Pipeline code never shells
out to a platform CLI.

### 4.2 Runtime plane

`AgentRuntime` discovers runtime targets and provisions or wakes agents. Agent
templates are optional bootstrap material; workflow correctness comes from
`work show`, contracts, Guide content, and evidence validation.

### 4.3 Web plane

The Web route layer performs only:

```text
parse parameters -> call command function -> return JSON unchanged
```

It does not read manifests directly, call engines, or post-process facts.

## 5. Role model

| Role | Authoring responsibility | Forbidden shortcut |
|---|---|---|
| planner | Design and acceptance documents | Decompose the DAG or implement product code. |
| orchestrator | Full or incremental manifest DAG | Implement product code. |
| worker | Contract-bounded code, PR, and verification | Self-review or change platform state directly. |
| reviewer | Independent reproduction and verdict | Trust the author's summary or edit the deliverable. |
| acceptor | End-to-end acceptance results | Change scope, fix code, or infer untested passes. |

Architect, backend, frontend, and PM are capability profiles, not extra
lifecycle roles. Their active role is determined by the dispatched task.

## 6. Command tree

```text
omac
  plan    create | confirm | resume | check | show
  dag     check | show | run | status | tick
  node    show | retry | accept | abandon
  work    show | submit
  init    interactive setup | --check
  config  get | set
  guide   workflow | roles | role | artifact | recovery
  web     read-only local dashboard
```

Argument errors print the relevant complete help. Agent-first `work` commands
return structured JSON errors when JSON output is active.

## 7. Exit-code contract

| Code | Meaning |
|---:|---|
| `0` | Success or full DAG convergence. |
| `1` | Generic error. |
| `2` | Platform or network error. |
| `3` | Authentication error. |
| `5` | Validation, lint, or evidence-schema failure. |
| `10` | One-round tick advanced work but has not converged. |
| `20` | Caller decision required; stdout carries a structured report. |

Business code raises `OmacError` subclasses. It does not scatter `sys.exit`
calls through pipeline logic.

## 8. Output contract

- stdout carries command data in `json` or `table` form.
- stderr carries progress events, warnings, and next-action hints.
- `work show` and `work submit` default to JSON.
- Progress logs default to human text; `--log-format json` emits JSON Lines.
- JSON keys, enums, commands, paths, URLs, and user/platform text are not
  translated.

The project-level `language` setting controls OMAC-authored prose. `en` is the
default; `cn` selects Simplified Chinese. Interactive `omac init` asks once and
writes the value to `.omac/config.yaml`.

## 9. Configuration and persistence

Project configuration and manifests are YAML committed with the repository.
There is no hidden SQLite state.

```yaml
language: en
engine: multica
workspace: <workspace-id>
project: <project-id>
roles:
  planner: planner-agent
  orchestrator: orchestrator-agent
  workers: [worker-a, worker-b]
  reviewers: [reviewer-a]
defaults:
  max_parallel: 4
  poll_interval: 30
  coverage_gate: 90
retry:
  worker: 3
  ci: 3
  review: 3
  merge: 3
workflow:
  human_in_loop: true
  review: true
  acceptance_doc: true
  goal_required: false
```

Resolution order for engine settings is project config, environment variables,
then explicit command arguments. Language intentionally has no command flag or
environment override: the saved project choice is the single source of truth.

## 10. Lifecycle

### 10.1 Initialize

`omac init` selects language, engine, workspace, optional Multica project,
optional agent templates, role mappings, concurrency, retry limits, and workflow
defaults. `omac init --check` validates configuration and reachable platform
facts without modifying files.

### 10.2 Plan

`omac plan create` produces:

1. A design document.
2. Reviewed repository-wide project rules written to root `AGENTS.md`.
3. An executable acceptance document.
4. A reviewed manifest DAG.

Planner-authored plans submit the design and project rules together. Both are
stored as work-item attachments and reviewed as one decision. `--doc` skips
planner authoring and does not update `AGENTS.md`.

Human confirmation can pause after design or acceptance. `plan resume` continues
the same pipeline rather than creating parallel facts.

### 10.3 Run the DAG

`omac dag run` is a foreground process. Each round:

1. Reconciles manifest and platform state.
2. Collects submitted results.
3. Runs machine gates, CI, review, and merge transitions.
4. Dispatches ready nodes up to the parallel limit.
5. Saves manifest state and emits progress.

The process ends only at convergence, an explicit time/round boundary, or exit
20. A caller must not claim continuous supervision when no foreground run is
active.

### 10.4 Agent execution

`work show` returns:

- `task`: kind, phase, status, identity, dependency and bounce facts.
- `context`: issue body, contract, upstream references, deliverable, setup, and
  prior review where applicable.
- `protocol`: the one current action.
- `authority`: conflict resolution order.
- `guide_refs`: minimal static topics.
- `submit`: exact executable delivery command.

`work submit` validates the phase-specific deliverable and atomically records
metadata before the loop advances state.

### 10.5 Final acceptance

After inner nodes converge and merge, the acceptor executes every acceptance
flow against the integration branch. Failed flows produce structured notes. The
orchestrator adds incremental fix nodes; completed nodes remain immutable facts.

## 11. Manifest contracts

Every node is the smallest independently developable, testable, PR-able, and
reviewable unit. A node contract includes:

- `objective`
- `source_of_truth`
- `required_contracts`
- `acceptance`
- `non_goals`
- `verification_commands`
- `integration_gates`
- `pr_base`
- `coverage_gate`
- optional `scope_paths`

`blocked_by` contains only true runtime prerequisites. `scope_paths` expresses
primary ownership, not an exhaustive file allowlist. Necessary supporting files
are permitted when the PR or verification explains why they serve the contract.

## 12. Evidence gates

### Worker verification

Records exact commands and exit codes, integration gates, coverage, PR base,
and reproducible `env_setup`. The PR must be ready for review, not draft.

### Reviewer report

Records review goals, independent test and integration reruns, coverage checks,
acceptance mapping, gate mapping, blockers, and nits. Pass forms have no
blockers; reject has actionable blockers.

### Final acceptance results

Contains exactly one result for every acceptance flow. Status is `pass` or
`fail`; every failure includes reproducible notes.

## 13. Recovery

Exit 20 is not success. The Controller reads `dag status`, `node show`, and, when
available, `work show` for the node issue. It then chooses:

- retry with new facts or a changed worker;
- accept a known risk with Human authority;
- abandon an optional capability with Human authority;
- repair or split the manifest contract.

Hard-dependent downstream work stays blocked. Independent branches continue.

## 14. Completion definition

A change is complete only when:

- it stays within approved scope;
- pipeline and CLI code depend only on engine interfaces;
- new behavior has regression coverage;
- `python3 -m pytest tests/` passes;
- documentation and Guide content are updated where behavior changed.

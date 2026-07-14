# oh-my-multica

[![CI](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaohei-info/oh-my-multica/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/xiaohei-info/oh-my-multica)

**Deterministic orchestration for multi-agent software delivery.**

`oh-my-multica`—the CLI and Python package are named `omac`—turns a software
change into a contract-backed manifest DAG. Agents plan, build, review, and
accept work in parallel; OMAC owns the deterministic loop, evidence checks, and
state transitions.

[English](README.md) | [简体中文](README.zh-CN.md)

## What OMAC does

Long-running agent work usually fails at the seams: requirements drift, task
state is inferred from chat, a reviewer trusts an author's summary, or a loop
stops without anyone noticing. OMAC makes those seams explicit.

- A planner-authored plan becomes a reviewed design, repository-wide project
  rules in `AGENTS.md`, an acceptance document, and a manifest DAG.
- Each DAG node has one owner, one reviewer, a bounded contract, verification
  commands, and an integration gate.
- `omac dag run` advances the graph in the foreground until it converges or
  returns exit 20 with a structured decision report.
- Workers submit evidence; reviewers reproduce independently; final acceptance
  records one pass/fail result for every acceptance flow.
- State lives in the manifest and work platform, so a rerun resumes instead of
  starting over.

OMAC is intentionally not a prompt that asks an LLM to supervise other LLMs.
The CLI drives the loop; planner, orchestrator, worker, reviewer, and acceptor
are finite jobs dispatched by that loop.

## Who uses which interface?

| Interface | Primary user | Contract |
|---|---|---|
| Platform issue | Human | The issue has one agent bootstrap command: `omac work show <id> --output json`. |
| `omac work show` / `omac work submit` | Agent | Both default to JSON. Read current facts from `show`, then use the exact returned `submit` command. |
| `omac guide ...` | Agent | Static knowledge only. Load the minimal topics listed in `guide_refs`; instance facts win on conflict. |
| `omac dag ...`, `omac node ...`, `omac web` | Operator | Inspect progress, operate the deterministic loop, and make explicit exit-20 decisions. |

```bash
# Agent bootstrap: JSON is the default
omac work show "$ISSUE_ID"

# Human-readable task view
omac work show "$ISSUE_ID" --output table
```

## Prerequisites

Every machine that runs OMAC needs:

- Python 3.10 or later.
- `pipx` for an isolated CLI installation.
- For the Multica engine: the `multica` CLI on `PATH` and already authenticated.

The mock engine has no external dependency. Use it for local demos, CI, and a
first run.

## Install

OMAC is distributed from this repository rather than public PyPI.

```bash
# Linux
python3 -m pip install --user pipx --break-system-packages
pipx ensurepath

# macOS
brew install pipx
pipx ensurepath
```

Open a new shell, then install OMAC:

```bash
git clone git@github.com:xiaohei-info/oh-my-multica.git
cd oh-my-multica
pipx install .

omac --version
omac init --check
```

To update an existing checkout:

```bash
git pull
pipx reinstall omac
```

For an offline runtime, build a wheel on a machine with the repository, copy it
to the target, then run `pipx install omac-1.0.0-py3-none-any.whl`.

## First run with the mock engine

The following commands run from the repository root. The mock workspace has
three agents: `alice`, `bob`, and `charlie`.

### 1. Create project configuration

For a human, `omac init` is an interactive wizard. Its first question chooses
the output language (`en` by default, or `cn`); the choice is saved as
`language` in `.omac/config.yaml`.

```bash
omac init
```

For CI or an agent, write the same configuration declaratively, then run the
health check. Non-interactive setup defaults to English; set `language` to `cn`
when the project should use Simplified Chinese.

```bash
omac config set language en
omac config set engine mock
omac config set workspace mock-workspace
omac config set roles.planner alice
omac config set roles.orchestrator bob
omac config set roles.workers '["alice"]'
omac config set roles.reviewers '["charlie"]'
omac config set workflow.human_in_loop false
omac config set workflow.acceptance_doc true
omac config set workflow.goal_required true
omac init --check
```

For the mock engine, use only `alice`, `bob`, and `charlie` in role mappings.

### 2. Produce a plan and manifest DAG

`omac plan create` runs the plan → acceptance → decomposition pipeline. It uses
the project `workflow` settings by default. `--doc` starts from an existing
design document; `--no-review`, `--no-acceptance`, and `--no-confirm` change a
single invocation only. Planner-authored plans must submit both the design and
project rules; after the pipeline converges, OMAC updates the managed section of
root `AGENTS.md`. The `--doc` path skips that update.

```bash
omac plan create --name login-renewal --goal "Renew an expired login session"
```

To inspect a ready-made manifest before planning your own work:

```bash
cat tests/fixtures/smoke_p1.yaml
```

### 3. Run the deterministic loop

```bash
cp tests/fixtures/smoke_p1.yaml /tmp/smoke.yaml

# Run in the foreground until convergence (exit 0)
omac dag run /tmp/smoke.yaml

# Inspect without advancing
omac dag status /tmp/smoke.yaml

# Advance exactly one round
# exit 0: converged; exit 10: still advancing; exit 20: caller decision needed
omac dag tick /tmp/smoke.yaml
```

Use `node show`, `node retry`, `node accept`, or `node abandon` only after an
exit-20 report. OMAC never silently retries a failed node.

### 4. Let agents load only the knowledge they need

```bash
omac guide
omac guide workflow
omac guide roles
omac guide role planner
omac guide role worker
omac guide role reviewer
omac guide artifact manifest
omac guide artifact evidence
omac guide recovery
```

For a dispatched task, do not pre-read the whole guide set. Run `work show`,
then load only its `guide_refs`.

## Command map

```text
omac
  CORE
    plan     create | confirm | resume
    dag      check | show | run | status | tick
    node     show | retry | accept | abandon
  WORK
    work     show | submit
  SETUP
    init     interactive configuration / --check health check
    config   get | set
  GUIDE
    guide    workflow | roles | role <name> | artifact <name> | recovery
  WEB
    web      local read-only dashboard
```

Run `omac <command> --help` for the current command contract. Argument errors
include complete help so that an agent can correct the next invocation without
guessing.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Success; every DAG node converged to `done`. |
| `1` | Generic error. |
| `2` | Platform or network error. |
| `3` | Authentication error, such as an unauthenticated platform CLI. |
| `5` | Validation failure, including lint or evidence-schema failure. |
| `10` | Work is still advancing; emitted only by one-round tick mode. |
| `20` | The caller must make a decision; stdout includes a structured report. |

## Architecture boundaries

Pipelines and CLI commands use only the engine `WorkItemStore` and
`AgentRuntime` interfaces. They never invoke platform CLIs directly. Platform
adapters own Multica, GitHub, and future Linear or Jira integration.

The Web layer only parses parameters, calls the matching command function, and
returns the command's JSON unchanged. Human, agent, and Web callers therefore
see the same facts.

## Development

For an editable local install:

```bash
pip install -e .
pip install pytest
python3 -m pytest tests/ -q -m "not live"
python3 -m pytest tests/ -q -m live
```

The `live` suite requires an authenticated Multica environment. A change is not
complete until the full test suite passes.

## More information

- `CHANGELOG.md` records user-visible changes.
- `omac guide workflow`, `omac guide role <name>`, and
  `omac guide artifact <name>` provide packaged runtime guidance.
- `omac <command> --help` is the authoritative command reference.

## License

[MIT](LICENSE)

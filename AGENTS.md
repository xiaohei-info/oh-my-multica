# AGENTS.md — Repository Development Conventions

This file contains implementation guardrails specific to the `oh-my-multica`
(`omac` CLI) repository. Cross-project engineering practices are intentionally
omitted; only repository-specific, non-negotiable constraints belong here.

## Hard Rules

**The pipeline and CLI layers may call only the engines' `WorkItemStore` and
`AgentRuntime` interfaces. They must never shell out to platform CLIs directly.**
Platform CLI calls (such as `multica` and `gh`) are encapsulated in engine
adapters. This rule allows future Linear or Jira integrations to add adapters
without changing the pipeline, and it is a code-review hard stop.

The web routing layer may only parse parameters, call command functions, and
return JSON unchanged. It must not read manifests, call engines, or transform
data. Humans, agents, and the web interface must always see the same facts.

## Exit-Code Contract (§5.1 — Stable and Safe for Script Branching)

| Code | Meaning |
|---|---|
| `0` | Success; every node in the DAG has converged to `done`. |
| `1` | General error. |
| `2` | Platform or network error. |
| `3` | Authentication error, such as a platform CLI that is not signed in. |
| `5` | Validation failure, such as lint or the evidence schema. |
| `10` | Still advancing; used only by single-round tick mode. |
| `20` | Caller decision required; includes a structured report. |

Business-layer `OmacError` subclasses map to their corresponding exit codes.
Do not scatter `sys.exit` calls. The exit-code contract must remain backward
compatible.

## Terminology (§10.2 — Required in User-Facing Output and Documentation)

| Required term | Do not use |
|---|---|
| result collection (`collect_results`) | harvest |
| running nodes (`running_nodes`) | in-flight |
| ready nodes (`ready_nodes`) | frontier |

Use the required terms in identifiers, error messages, guide text, and design
documents. Avoid ad hoc translated jargon.

## TDD

1. **Write tests before implementation.** New or changed executable behavior
   needs regression coverage. Cover at least the main path, boundaries, and
   known risks.
2. **Delivery = code + tests + necessary documentation.** Changes to source
   code, tests, build/package configuration, schemas, or executable behavior
   require a green `python3 -m pytest tests/` run for completion.
3. **Use proportional verification for documentation-only changes.** A diff
   containing only Markdown or static diagram assets does not require the full
   pytest suite. Run `git diff --check`, verify local links and assets, and
   parse or render changed formats when tooling is available. If documentation
   is packaged or machine-consumed, run its relevant targeted tests.
4. **Verify independently before completion.** Writing code is not completion.
   Completion requires objective evidence from tests, builds, or actual command
   output.

## Errors Teach Users

User-facing output and errors must state what is missing, how to fix it, and a
copyable command. For invalid arguments, print the error and the command's full
help text so an agent can correct itself after one mistake.

## Definition of Done

A change is complete only when all of the following are true:

- [ ] It stays within the planned scope.
- [ ] It consumes only `WorkItemStore` and `AgentRuntime` interfaces; it does
  not shell out to platform CLIs directly.
- [ ] Verification is proportional to the change: full
  `python3 -m pytest tests/` for code/configuration/behavior changes; targeted
  document checks (and relevant document tests when applicable) for
  documentation-only changes.
- [ ] Documentation and guides are updated when necessary.

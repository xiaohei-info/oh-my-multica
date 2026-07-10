# Flexible DAG Scope Paths Design

## Problem

`contract.scope_paths` is optional manifest metadata intended to describe the primary
code ownership boundary of a DAG node. The worker issue body currently renders it as
an exact allowlist with the rule "out-of-scope changes are rejected". That wording
turns an ownership hint into a hard file whitelist.

This blocks valid work when a node needs a small supporting change outside its main
module, such as adding an approved dependency to `package.json`, updating a lockfile,
or adjusting shared test/build configuration. The auth node in the snake DAG is a
concrete failure: its security contract requires Argon2id or bcrypt, but the worker
cannot add the dependency because `package.json` was not predicted in `scope_paths`.

## Decision

Keep `scope_paths` as backward-compatible structured metadata, but define it as the
node's **primary ownership scope**, not an exhaustive allowlist.

- Files matching `scope_paths` are the expected center of the change.
- A worker may change supporting files outside that list when the change is necessary
  to satisfy the node contract.
- The worker must explain supporting changes in the PR or verification evidence.
- Reviewers reject unrelated expansion, parallel-boundary violations, and `non_goals`
  violations. They do not reject a change solely because a necessary supporting file
  is outside `scope_paths`.
- `non_goals`, shared-contract rules, verification commands, coverage gates, PR base,
  and review remain hard constraints.

The Orchestrator should list stable module ownership paths, not attempt to predict
every dependency manifest, lockfile, migration, generated artifact, or build config
that implementation may legitimately touch.

## User-Facing Changes

The develop issue body will replace the current hard rule:

> Code is limited to these paths; changes outside them are rejected.

with a primary-scope rule that explicitly permits necessary supporting files and
requires an explanation. Worker and reviewer guides will use the same terminology.
The manifest guide will document that `scope_paths` is optional and non-exhaustive.

No manifest schema migration is required. Existing manifests continue to parse and
serialize without changes.

## Current DAG Recovery

For `.omac/贪吃蛇手游.yaml`, add `package.json` to the auth node's primary scope so
the current issue communicates the immediate dependency work clearly. Reset
`auth-server-api` to `todo` with `omac node retry`, reuse AITEAM-777, and resume
`omac dag run`.

The worker must still select an implementation allowed by the approved security
contract: Argon2id, or bcrypt with cost at least 12. Scrypt remains disallowed.

## Compatibility And Risk

The change is wording and guidance only; no new enforcement engine is introduced.
This avoids breaking existing manifests and keeps the system simple. The main risk is
scope creep, controlled by the existing `non_goals`, contract evidence, PR review,
and the requirement to justify supporting-file changes.

## Verification

- Dispatch tests prove `scope_paths` renders as primary scope, not an exact allowlist.
- Dispatch tests prove supporting-file changes are permitted only when contract-related
  and must be explained.
- Guide tests/searches prove Orchestrator, worker, reviewer, and manifest docs use the
  same semantics.
- Full `python3 -m pytest tests/` passes.
- The installed `omac` command is refreshed from the current checkout.
- AITEAM-777 is retried with the updated contract and starts one worker run.

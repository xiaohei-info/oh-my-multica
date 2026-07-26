# Multica bridge — Atlas control plane, Artemis planning surface

Date: 2026-07-26
Status: implemented (shadow pilot ready)

This document describes how OMAC adapts to the Multica workflow: the human
board stays in Multica with five lifecycle stages, hard or ambiguous work stops
at a Mark-owned plan gate, and the machine DAG plane delivers through
review-before-PR sequencing with an external merge authority. The bridge is the
thinnest composition over the existing `WorkItemStore` / `AgentRuntime`
interfaces — it does not recreate the scheduler.

## Control-plane boundary

- **Atlas is the sole OMAC/Multica state writer.** OMAC owns post-intent
  delivery state, dependencies, evidence validation, recovery, and acceptance.
  Do not run active-active OMAC controllers.
- **Multica remains the human board**, the assignee surface, and the source
  pointer. Machine DAG work lives outside human project boards in a Delivery
  Operations machine namespace; every machine manifest declares
  `meta.source: {project, issue}` pointing back to its human-board origin.
- **Artemis is Mark's planning and visual-acceptance surface initially.** It
  may publish plans and visual evidence; it does not own OMAC state and is not
  an autonomous worker in v1 (runner metadata is validated and recorded only).

## Human plan gate

Hard/ambiguous/product/security/migration/cross-project work, and UI work
without an approved DESIGN.md, is marked with a machine-only gate on the node:

```yaml
nodes:
  - id: hard-node
    worker: alice
    gate:
      human_plan: true
```

A gated node is never dispatched until the validator records an immutable plan
snapshot. While held, the node stays `todo` (not `blocked`, not failed); the
loop reports `needs_decision` (exit 20) with a structured report whose
`next_actions` lead with a copyable repair command.

Only the validator unlocks the gate:

```text
PlanReturn path=/absolute/path/to/plan.md
PlanReturn artifact=https://artifactd.example/...
PlanReturn host=artemis path=/absolute/path/to/plan.md sha256=<digest>
```

```bash
omac bridge submit-plan-return <manifest> --text "PlanReturn path=/abs/plan.md"
```

- Malformed input (comment novels, relative paths, unsupported schemes,
  unknown/duplicate keys, ambiguous combinations) exits 5 with the approved
  forms.
- Missing/unreadable/mutating files, hash mismatches, non-allowlisted hosts,
  or a missing fetch adapter exit 20 with a structured report containing a
  copyable `repair` line.
- The snapshot is content-addressed (`<plan_gate.store_dir>/<sha256>.md`),
  written atomically, and recorded in `manifest.meta.plan_snapshot`.
- The CLI embeds no fetch and no shell: `artifact=` / `host=` forms require an
  injected narrow `fetch(source) -> bytes` adapter through the bridge API
  (`omac.bridge.multica.submit_plan_return(..., fetch=...)`).
- `plan_gate.allowed_hosts` defaults to empty — the host form fails closed.

Rollback: `omac.bridge.multica.revoke_plan_snapshot(manifest, path)` removes
the recorded snapshot and the gate locks again. Disabling the feature is
equally simple — remove `gate.human_plan` from the manifest and upstream
dispatch behavior applies unchanged.

## Delivery sequencing (adapted mode)

Both gates default off; upstream default sequencing is byte-identical when
they are off.

```yaml
delivery:
  review_before_pr: true   # worker delivers branch + exact tip; independent
                           # review runs before any PR exists; a deterministic
                           # publisher opens the draft PR after green review
                           # on the same tip
  external_merge: true     # OMAC never merges; it waits for validated
                           # external merge evidence bound to the approved
                           # pr_url + tip_sha
```

1. Worker submits `artifacts.branch` + `artifacts.tip_sha` (strict 40-hex),
   no PR URL.
2. The independent reviewer inspects the exact branch/tip and records
   `review_report.tip_sha`.
3. `run_pr_publish` refuses stale-tip reviews and unbound reviews, then calls
   `WorkItemStore.publish_draft_pr` — the platform CLI (`gh pr create --draft`)
   stays inside the engine adapter.
4. In external merge mode OMAC enters a structured waiting state
   (`artifacts.external_merge_wait`) and never invokes a merge command. The
   authorized external merge automation delivers evidence:

```bash
omac bridge submit-merge-evidence <manifest> --issue <id> --evidence-file merge.json
```

Only evidence with `merged: true` and the exact approved `pr_url` + `tip_sha`
advances; stale, wrong, or malformed evidence exits 5 without any write.

## Five-stage parent projection

`omac bridge status <manifest>` projects the machine DAG onto the human board
without any visible workflow labels:

| Human stage | Platform status | Meaning |
|---|---|---|
| Intake | `backlog` | Unaccepted or unnormalized work |
| Plan | `todo` | Ready contract or Mark-owned human plan gate |
| Build | `in_progress` | One leased implementation owner (includes `ci_check`) |
| Verify | `in_review` | Independent QE, PR, merge, deployment, acceptance (includes `merging`) |
| Done | `done` | Terminal evidence validated (`abandoned` counts as satisfied) |

`blocked` / `failed` / `cancelled` are exception states, not lifecycle stages;
the projection lists them separately and never silently folds them into a
stage. The parent stage is the furthest stage reached by active (non-done)
nodes; completed nodes never drag the parent backwards.

## Machine isolation

```yaml
machine:
  project: delivery-operations
  namespace: omac
```

When the `machine` block is configured (both keys required), the bridge
requires the manifest to declare `meta.source: {project, issue}` and a
`meta.namespace` equal to the configured machine namespace. Violations exit 5
from `omac bridge dry-run`. When the block is absent, validation is a no-op
and upstream behavior is unchanged.

## Bridge commands

| Command | Writes | Exit codes |
|---|---|---|
| `omac bridge dry-run <manifest>` | none | 0 observed / 5 isolation violation |
| `omac bridge status <manifest>` | none | always 0 (observation) |
| `omac bridge submit-plan-return <manifest> --text ...` | manifest meta + plan snapshot store | 0 unlocked / 5 malformed / 20 unresolvable |
| `omac bridge submit-merge-evidence <manifest> --issue <id> --evidence-file <f>` | `artifacts.external_merge` on the work item | 0 recorded / 5 rejected |

Exit-20 recovery: the report's `repair` line is copyable; fix the plan input,
resubmit, and rerun `omac dag run`. Rollback: revoke the plan snapshot or turn
the feature gates off — no machine state migrates because all state lives in
the manifest and the platform, as before.

## Artifactd shadow pilot

`tests/fixtures/shadow/artifactd/` contains the non-UI shadow configuration
and manifest. It runs entirely on the mock engine: no assignment, PR, merge,
deploy, or product mutation can leave the process, and live Multica queues and
autopilots are untouched (they remain the rollback path during the pilot).
`tests/test_shadow_artifactd.py` proves, end to end: hard-plan block/unlock,
machine issue isolation, review-before-PR ordering, the external merge
handoff, exit-20 recovery, and rollback.

CLI smoke (plan gate block/unlock and exit-20 recovery):

```bash
cp -r tests/fixtures/shadow/artifactd /tmp/omac-shadow
mkdir -p /tmp/omac-shadow/.omac
mv /tmp/omac-shadow/config.yaml /tmp/omac-shadow/.omac/config.yaml
cd /tmp/omac-shadow
omac bridge status manifest.yaml       # projection: parent stage plan
omac bridge dry-run manifest.yaml      # shadow-config held-by-plan-gate
omac dag run manifest.yaml             # exit 20: human plan gate holds dispatch
omac bridge submit-plan-return manifest.yaml \
  --text "PlanReturn path=/tmp/omac-shadow/plan.md"   # exit 0: gate unlocked
omac dag run manifest.yaml             # dispatches the worker
```

The CLI discovers project configuration at `.omac/config.yaml` next to the
manifest, which is why the fixture's `config.yaml` is moved there first.
The full loop through draft-PR publication and external-merge evidence runs
in `tests/test_shadow_artifactd.py`: the mock engine keeps work items in
memory per process, so the worker's branch+tip delivery and the merge
evidence are injected through in-process hooks that a multi-process CLI run
cannot share. The mock engine's generic auto-delivery is upstream-shaped
(`pr_url`), which the adapted evidence gate correctly rejects as
`artifacts.branch is required` — fail closed, no dispatch onward.

## Label compatibility dictionary

No label is deleted during the shadow pilot. Deletion happens only after
reference inventory, parity verification, and a rollback snapshot.

Disposition meanings:

- `KEEP-HIDDEN`: retain temporarily as a machine authorization/compatibility
  token, exclude from Mark's board filters.
- `MIGRATE`: move meaning to native status, assignee, manifest metadata, or
  evidence, then delete after shadow parity.
- `DELETE`: remove after references and historical filters are migrated.
- `KEEP-VISIBLE`: genuinely useful human/domain signal.

| Existing label | Disposition | Target |
|---|---|---|
| `priority:P0` | MIGRATE | native priority |
| `diff:easy` | MIGRATE | manifest complexity |
| `diff:medium` | MIGRATE | manifest complexity |
| `diff:hard` | MIGRATE | human-plan predicate |
| `needs-plan` | KEEP-HIDDEN | Plan gate metadata during bridge |
| `plan:done` | KEEP-HIDDEN | validator-owned compatibility token |
| `skip-plan` | KEEP-HIDDEN | explicit bounded-work authorization |
| `needs-codex` | DELETE | capability/runner selection |
| `compact-ok` | DELETE | manifest capability constraint |
| `needs-sol` | DELETE | exit-20/escalation policy |
| `wf:triaged` | MIGRATE | Intake/Plan status |
| `wf:planning` | MIGRATE | Plan status |
| `wf:implementing` | MIGRATE | Build status |
| `wf:verifying` | MIGRATE | Verify status + evidence state |
| `wf:pr-drafted` | MIGRATE | PR artifact state |
| `wf:done` | MIGRATE | native done |
| `wf:failed` | MIGRATE | blocked + structured failure |
| `auto-run` | KEEP-HIDDEN | explicit implementation authorization |
| `ready-for-agent` | DELETE | DAG-ready state |
| `wf:ready` | DELETE | DAG-ready state |
| `route:grok` | DELETE | manifest/runtime assignment |
| `route:glm` | DELETE | manifest/runtime assignment |
| `route:codex` | DELETE | manifest/runtime assignment |
| `route:kimi` | DELETE | manifest/runtime assignment |
| `mode:delivery` | MIGRATE | issue/manifest work type |
| `mode:investigation` | MIGRATE | issue/manifest work type |
| `ready-for-human` | MIGRATE | Mark assignee + Plan/blocked state |
| `hitl:merge` | KEEP-HIDDEN | Mark-owned merge gate during bridge |
| `hitl:decision` | KEEP-HIDDEN | exit-20 decision type |
| `hitl:needs-info` | KEEP-HIDDEN | exit-20 decision type |
| `hitl:safety` | KEEP-HIDDEN | exit-20 decision type |
| `hitl:budget` | KEEP-HIDDEN | exit-20 decision type |
| `needs-decoder` | KEEP-VISIBLE | domain signal if still used by project |
| `read-first` | KEEP-VISIBLE | human instruction if still used by project |
| `batch:1` | MIGRATE | DAG layer |
| `batch:2` | MIGRATE | DAG layer |
| `merge:auto` | KEEP-HIDDEN | explicit merge authorization |
| `stacked:root` | MIGRATE | DAG dependency metadata |
| `stacked:dependent` | MIGRATE | DAG dependency metadata |
| `stack-layer:1` | MIGRATE | DAG layer |
| `stack-layer:2` | MIGRATE | DAG layer |
| `stack-layer:3` | MIGRATE | DAG layer |
| `wf:investigating` | MIGRATE | issue/manifest work type + native status |
| `ready-for-delivery-auth` | KEEP-HIDDEN | explicit implementation authorization during bridge |
| `wf:human-gate` | KEEP-HIDDEN | Mark-owned gate during bridge |
| `wf:scoring` | DELETE | deterministic intake/manifest classification |
| `skip-scoring` | DELETE | scoring stage removed |

## Deferred (non-v1)

- **Artemis as an autonomous worker.** Runner metadata (runner class,
  preferred/actual host, expected tip, lease holder, lease expiry) is
  validated and recorded, but no lease enforcement exists in v1.
- **Artifact/host fetch adapters.** The bridge API accepts an injected narrow
  `fetch(source) -> bytes`; concrete Artifactd/Artemis adapters land with the
  live integration, not the shadow pilot.
- **Label deletion.** Only after reference inventory, parity verification,
  and a rollback snapshot.

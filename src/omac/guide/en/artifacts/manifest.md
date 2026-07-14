# Manifest artifact contract

## When to use it

Use this contract during `decompose` authoring or review. It turns approved
design and acceptance documents into a parallel, independently verifiable
manifest DAG, normally saved as `.omac/<name>.yaml`.

First run:

```bash
omac work show <issue-id> --output json
```

Use its task, context, authority, guide references, submit command, and agent
pool as instance facts. This guide does not override facts, existing manifests,
or incremental-decomposition context.

## Minimum valid example

The example shows the full contract shape. Replace `worker` and `reviewer` with
different members of the current agent pool.

```yaml
meta:
  name: login-renewal
nodes:
  - id: auth-renewal
    title: Implement session renewal
    worker: backend-agent
    reviewer: review-agent
    blocked_by: []
    contract:
      objective: Renew an expired session and replay the original request once
      source_of_truth:
        - docs/design.md#cross-module-contract
      required_contracts: []
      acceptance:
        - flow-login-renewal
      non_goals:
        - Do not change payment flows
      verification_commands:
        - python3 -m pytest tests/test_auth_renewal.py
      integration_gates:
        - name: auth-renewal-e2e
          layer: L1
          source_of_truth:
            - docs/design.md#acceptance-mapping
          delivery_goal: The sign-in renewal path works
          covers:
            - session-renewal
          acceptance_refs:
            - flow-login-renewal
          commands:
            - python3 -m pytest tests/test_auth_renewal_e2e.py
          required_metrics: {}
          artifacts: []
      pr_base: feature/login-renewal
      coverage_gate: 90
      acceptance_doc: null
      scope_paths:
        - src/auth/**
```

## Field semantics

### DAG granularity

Each node is the smallest independently PR/test/reviewable unit. Its worker can
develop, run `verification_commands`, and submit a PR independently; its reviewer
can decide pass/reject from that deliverable and contract.

Maximize parallel development. Keep splitting while another capability can have
an independent contract, test command, PR, and clear downstream effect. Stop
only when another split leaves file moving or trivial type/style changes with no
independent acceptance value, breaks a single transactional-consistency boundary,
or creates conflict that a stable shared contract/API cannot remove.

`blocked_by` lists only nodes truly required before execution. Prefer stable
contracts/APIs to reduce hard dependencies; references must exist and the graph
must be acyclic.

### Staying on target

- Contracts are code: import shared types; do not define them in parallel.
- Keep one source of truth: nodes reference design and acceptance anchors rather
  than copying authoritative prose.
- Split at stable contracts/APIs before declaring real runtime prerequisites.
- CI catches interface and boundary drift; reviewers judge semantic drift from
  objective, acceptance, and non-goals.

### Node fields

| Field | Meaning |
|---|---|
| `id` | Unique stable manifest ID. |
| `title` / `description` | Short explanation; description references facts, not copied design body. |
| `worker` / `reviewer` | Current-pool members; reviewer differs from worker. |
| `blocked_by` | Actual prerequisite node IDs; use `[]` when none. |
| `work_item_id` / `status` | Runtime-populated facts; do not invent them during authoring. |
| `contract` | The node's only implementation and review contract. |

### Complete contract

| Field | Meaning |
|---|---|
| `objective` | One-sentence deliverable result. |
| `source_of_truth` | Authoritative sections with data, edges, boundaries, and contracts. |
| `required_contracts` | Shared contract paths required before start; non-empty entries are linted for existence. |
| `acceptance` | Stable acceptance-document flow IDs. |
| `non_goals` | Adjacent scope explicitly forbidden. |
| `verification_commands` | Copyable node verification commands. |
| `integration_gates` | Cross-module or end-to-end gates required after delivery. |
| `pr_base` | Required integration branch for the PR. |
| `coverage_gate` | Number from 0 to 100; default 90. |
| `acceptance_doc` | Optional structured acceptance context when the instance contract needs it. |
| `scope_paths` | Optional primary code ownership for stable boundaries and lower parallel conflict. |

Each integration gate has `name`, `layer`, `delivery_goal`, and non-empty
`source_of_truth`, `covers`, `acceptance_refs`, and `commands`. If present,
`required_metrics` is an object and `artifacts` is a list. Worker verification
and reviewer reports repeat gate names, commands, sources, and goals from the
contract.

Contracts must be independently executable by low-reasoning-budget workers;
state edge cases, prohibited scope, verification entry points, and integration
outcomes. `scope_paths` is not an exhaustive file whitelist. Supporting tests,
lock files, migrations, generated files, or build configuration may change when
the contract requires them; the worker explains why. Review judges contract fit,
non-goals, and parallel boundaries, not merely path membership.

## Validation gates

1. YAML parses; every node has `id` and `worker`.
2. Worker and reviewer are in the agent pool and are different people.
3. `blocked_by` references valid nodes; the DAG has no cycle; incremental IDs do
   not collide with existing nodes.
4. Contract `objective`, `source_of_truth`, `acceptance`, `non_goals`,
   `verification_commands`, `integration_gates`, and `pr_base` are non-empty.
5. Every integration gate's required scalars and lists are non-empty; metrics and
   artifacts have correct types.
6. `coverage_gate` is 0–100 and required-contract paths exist.
7. With an acceptance document, every `contract.acceptance` references a real flow.
8. `meta.closeout_node`, when present, references a manifest node.

## Common errors → corrections

| Error | Correction |
|---|---|
| One node contains several independently deliverable capabilities | Split at stable contracts/APIs into independent PR/test/review units. |
| `blocked_by` added just to show order | Keep only real prerequisites; use contracts to decouple the rest. |
| Contract has an objective but no verification | Fill every required field and at least one complete integration gate. |
| `acceptance` is a natural-language summary | Use stable acceptance-document flow IDs. |
| `scope_paths` rejects every other file | Permit required supporting files and explain them in PR or verification. |
| Design copied into `description` | Keep source-of-truth anchors only. |

## Submit

Re-read `work show` and use its exact command:

```bash
omac work submit <issue-id> --manifest-file <file>
```

Fix parser or lint errors one by one. Do not bypass validation or manually move
platform state.

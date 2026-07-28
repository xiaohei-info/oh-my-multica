# Manifest artifact contract

## When to use it

Use this contract during `decompose` authoring or review. It turns approved
design and acceptance documents into a parallel, independently verifiable
manifest DAG, normally saved as `.omac/<name>.yaml`.

The same contract guides `amendment` authoring/review, but an amendment submits
generic manifest operations rather than a replacement live manifest. See
`omac guide recovery` for CAS and minimal-resume rules.

First run:

```bash
omac work show <issue-id> --output json
```

Use its task, context, authority, guide references, submit command, and agent
pool as instance facts. This guide does not override facts, existing manifests,
or incremental-decomposition context.

## Canonical plan-to-DAG handoff

The decompose Reviewer reviews the submitted manifest deliverable. After that
review passes, `omac plan create/resume` writes a canonical execution manifest
from the same `Manifest` / `Contract` model that `omac dag run` loads:

- An explicit default `coverage_gate: 90` may be omitted; loading restores 90.
  Non-default coverage gates remain in the canonical file.
- Empty `required_contracts: []` and null per-node `acceptance_doc` values may be
  omitted; loading restores their empty/none execution meaning.
- The reviewed acceptance artifact is written once as the sibling file named by
  `meta.acceptance_file`. The manifest records `acceptance_required`, `plan_id`,
  and `source_issues`; it does not duplicate that authoritative document in
  every node contract.
- `work_item_id` and `status` are runtime facts. `omac dag run` supplements them
  as nodes are dispatched and advanced without changing the node's executable
  contract.

This is semantic canonicalization, not a second review object. If
`acceptance_required` is true and the configured acceptance file is missing,
DAG execution is fail-closed and explains how to restore the reviewed artifact.

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
      acceptance_contributions:
        - flow_id: flow-login-renewal
          action_ids:
            - ACT-LOGIN-RENEWAL-01
            - ACT-LOGIN-RENEWAL-02
      acceptance_refs:
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
      evidence_mode: fixture
      produces:
        - artifact_id: auth-renewal-component
      consumes: []
  - id: auth-renewal-e2e
    title: Verify session renewal journey
    worker: integration-agent
    reviewer: review-agent
    blocked_by: [auth-renewal]
    contract:
      objective: Independently execute and prove the complete renewal flow
      source_of_truth: [docs/design.md#acceptance-mapping]
      acceptance_claims: [flow-login-renewal]
      acceptance_refs: [flow-login-renewal]
      non_goals: [Do not reimplement the renewal component]
      verification_commands: [python3 -m pytest tests/e2e/test_auth_renewal.py]
      integration_gates:
        - name: auth-renewal-flow
          layer: L2
          source_of_truth: [docs/design.md#acceptance-mapping]
          delivery_goal: The complete renewal flow is independently reproducible
          covers: [session-renewal-flow]
          acceptance_refs: [flow-login-renewal]
          commands: [python3 -m pytest tests/e2e/test_auth_renewal.py]
      pr_base: feature/login-renewal
      evidence_mode: live
      produces:
        - artifact_id: auth-renewal-flow-evidence
      consumes:
        - artifact_id: auth-renewal-component
          producer: auth-renewal
          evidence_mode: artifact
```

## Field semantics

### DAG granularity

Each node is the smallest independently PR/test/reviewable unit, and it must be
complete, production-usable, and independently acceptable. Its worker can
develop, run `verification_commands`, and submit a PR independently; its
reviewer can decide pass/reject from that deliverable and contract. Do not
declare a directory shell, interface skeleton,
fixed return value, placeholder, or production synthetic-data fallback complete.
If a node needs a later patch to acquire the value claimed by its objective,
redraw the contract boundary.

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
| `acceptance_claims` | Complete flows this node executes and independently proves; it does not repeat the flow Action list. |
| `acceptance_contributions` | Exact `{flow_id, action_ids}` implemented by this node; IDs may reference only `kind: business-action` entries. |
| `acceptance_refs` | Trace-only flow references; they create no Worker or Reviewer acceptance obligation. |
| `acceptance` | Legacy complete-flow claim field. It remains readable with its original meaning and cannot be mixed with the new fields. |
| `non_goals` | Adjacent scope explicitly forbidden. |
| `verification_commands` | Copyable node verification commands. |
| `integration_gates` | Cross-module or end-to-end gates required after delivery. |
| `pr_base` | Required integration branch for the PR. |
| `coverage_gate` | Number from 0 to 100; default 90. |
| `acceptance_doc` | Optional structured acceptance context when the instance contract needs it. |
| `scope_paths` | Optional primary code ownership for stable boundaries and lower parallel conflict. |
| `evidence_mode` | Optional primary evidence class: `fixture`, `artifact`, or `live`. It is required when `produces` or `consumes` is declared. |
| `produces` | Stable artifact IDs uniquely produced by this node, shaped as `{artifact_id}`. One artifact ID has one canonical producer. |
| `consumes` | Allowed external inputs shaped as `{artifact_id, producer, evidence_mode}`. The producer must be transitive upstream and declare the artifact. |

Each integration gate has `name`, `layer`, `delivery_goal`, and non-empty
`source_of_truth`, `covers`, `acceptance_refs`, and `commands`. If present,
`required_metrics` is an object and `artifacts` is a list. Worker verification
and reviewer reports repeat gate names, commands, sources, and goals from the
contract. Worker verification also maps every contract acceptance item to a
concrete business test through `business_tests` on a successful command. This
applies to complete flow claims and Action contributions, not trace-only refs.

The complete DAG forms an explicit responsibility matrix: every flow has one
canonical full owner; every `business-action` has a contribution owner; and the
full owner is or transitively depends on every contribution owner for that flow.
`flow-step` records belong to the full owner and are not copied into manifest
contributions. An upstream bootstrap cannot masquerade as an end-to-end owner,
and final closeout does not repeat nearly a thousand step IDs.

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
4. Contract `objective`, `source_of_truth`, at least one acceptance responsibility, `non_goals`,
   `verification_commands`, `integration_gates`, and `pr_base` are non-empty.
5. Every integration gate's required scalars and lists are non-empty; metrics and
   artifacts have correct types.
6. `coverage_gate` is 0–100 and required-contract paths exist.
7. With an acceptance document, claims/refs name real flows and contributions name real `business-action` IDs; every flow has one full owner, business-Action coverage is complete, and the owner dependency closure contains every contribution owner.
8. `meta.closeout_node`, when present, references a manifest node.
9. When a typed artifact boundary is present, `evidence_mode` must be valid;
   every consume producer must exist, be transitive upstream, and produce the
   named artifact. A fixture node cannot require live evidence. Legacy manifests
   that omit these fields keep their existing behavior and require no migration.

## Common errors → corrections

| Error | Correction |
|---|---|
| One node contains several independently deliverable capabilities | Split at stable contracts/APIs into independent PR/test/review units. |
| A node delivers only a skeleton, placeholder, or synthetic-data fallback | Make it a complete runnable and acceptable foundation capability, or merge it with the later work into one complete contract. |
| `blocked_by` added just to show order | Keep only real prerequisites; use contracts to decouple the rest. |
| Contract has an objective but no verification | Fill every required field and at least one complete integration gate. |
| A component writes a flow ID as a full claim | Declare its business-Action contributions and put the full claim on an integration/closeout node downstream of all contributors. |
| The full owner repeats every flow Action ID | Remove those duplicate contributions; DAG dependency closure connects the full owner to implementation owners. |
| Traceability is expressed as an acceptance obligation | Use `acceptance_refs`; trace refs require neither a full journey nor business-test evidence. |
| Legacy `acceptance` is mixed with new fields | Migrate it explicitly to `acceptance_claims`, then add contributions/refs; never silently reinterpret it. |
| `scope_paths` rejects every other file | Permit required supporting files and explain them in PR or verification. |
| Design copied into `description` | Keep source-of-truth anchors only. |

## Submit

Re-read `work show` and use its exact command:

```bash
omac work submit <issue-id> --manifest-file <file>
```

For a running-DAG amendment, use:

```bash
omac work submit <issue-id> --amendment-file <file>
```

Fix parser or lint errors one by one. Do not bypass validation or manually move
platform state.

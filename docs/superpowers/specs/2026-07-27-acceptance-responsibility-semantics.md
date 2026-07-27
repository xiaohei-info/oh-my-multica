# Acceptance responsibility semantics and review closure

## Production failure

An approved Open Agent Cluster manifest assigned `UJ-PUBLIC-ENTRY-001` to eight
nodes and `UJ-KERNEL-001` to six nodes. Local bootstrap nodes were therefore
reviewed as if they independently owned complete end-to-end journeys. Console
bootstrap could not prove the bilingual public-site journey; Go bootstrap was
sent through repeated rework for a Kernel/oacok journey that its contract could
not possibly deliver.

## Root cause

The old contract had one `acceptance: [flow-id]` list. That field carried three
different intentions without a discriminator:

1. complete end-to-end flow ownership;
2. a local contribution to one or more Actions;
3. requirement traceability only.

The manifest lint checked only that a value named an existing flow. Review
obligations converted every value into `Verify acceptance outcome <flow>`. The
decompose Reviewer received per-node contracts but no global responsibility
matrix, so it had no bounded obligation to find duplicate owners, missing
Action closure, or a local gate that could not support a complete claim.

This is why the problem was not found during Orchestrator review: neither the
data model nor the machine/review evidence exposed the distinction.

## New model

New contracts use three explicit fields:

- `acceptance_claims`: complete flows this node independently proves;
- `acceptance_contributions`: exact business `{flow_id, action_ids}` delivered by this node;
- `acceptance_refs`: trace-only flow references with no execution obligation.

The machine gate constructs the complete DAG matrix and rejects:

- a flow without exactly one complete owner;
- duplicate complete owners;
- a `business-action` without a contribution owner;
- unknown flows or Actions;
- a full claim whose owner is not equal to or transitively downstream of every
  contribution owner for the flow;
- mixed use of legacy `acceptance` and the new fields.

No Action-prefix or node-name heuristic is used. Acceptance v2 marks every step
as `business-action` or `flow-step`. Only business Actions enter the contribution
matrix; authority, setup, verification, evidence, and cleanup procedure belongs
to the canonical full owner. A node named `integration` receives no special trust.

## Reviewer behavior

Decompose review receives one compact `acceptance-responsibility:matrix`
obligation containing full owners, business-Action counts, contribution owners,
dependency closure, and exceptional IDs only. It does not repeat every Action
mapping already present in the manifest. The Reviewer inspects every reported
gap in one bounded pass.

Develop review creates full-flow obligations only for `acceptance_claims`,
Action-scoped obligations for `acceptance_contributions`, and no obligation for
`acceptance_refs`. Worker `business_tests` and Reviewer `acceptance_mapping`
cover the same responsibility targets.

## Compatibility and active-DAG transition

`contract.acceptance` remains readable and retains its published meaning as a
complete-flow claim. Runtime loading and existing DAG execution do not silently
reinterpret or discard it.

New plan authoring requires `omac.acceptance/v2` with explicit `action.id` and
`action.kind`, and
new decomposition rejects legacy `contract.acceptance` with a migration error.
`omac.acceptance/v1` remains readable. OMAC classifies a record with an embedded
`Action ID` as a business Action; other records become `flow-step` with the
deterministic identity `<flow-id>/STEP-<index>`. This preserves the current OAC
interpretation of 922 total steps and 495 explicit business Actions.

For an already-running DAG, upgrade does not rewrite completed or active nodes.
An Orchestrator amendment can translate affected pending/blocked contracts to
the explicit fields while preserving node IDs, PRs, verification attachments,
review history, and completed facts. Stage-level resume then continues from the
earliest invalid stage: contract-only changes require review again; unchanged
implementation evidence does not require another Worker run; a prior pass with
only merge observation failure resumes at merge. The amendment/resume command
surface is intentionally separate from this responsibility-model change.

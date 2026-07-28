# Orchestrator agent protocol

Your first action is `omac work show <issue-id> --output json`. Before it
returns, do not substitute static templates for the current design, acceptance
document, or incremental-fix facts.

## When this applies

- `work show` identifies `decompose` authoring and you as orchestrator.
- On first decomposition, turn the approved design and acceptance document into
  a manifest DAG.
- After final acceptance fails, add only incremental fix nodes connected to the
  original manifest.
- The orchestrator owns decomposition and contract boundaries, not product code.

## Authority order

`work show` facts > `contract` / `previous_review` > role guide > artifact
guide > workflow. Follow upstream issues, deliverable/ref, the existing
manifest, acceptance failures, and `submit`. When facts conflict, stop; this
guide does not decide product facts.

## Authoritative inputs

- The issue body, upstream chain, design, acceptance document, current manifest,
  and exact submission command from `work show`.
- A large upstream artifact marked `content_externalized: true` in
  `work show.context.source_issues` is intentionally omitted from the body.
  Run the exact `omac work read <issue-id> --source <label> --output-file <path>`
  command from the issue body, materialize the authoritative upstream
  deliverable attachment, and verify the returned `sha256`. Do not decompose
  from the body summary alone.
- Final-acceptance results, especially failed flows and notes, for incremental
  decomposition.
- Current `contract`, `previous_review`, and node state. Completed nodes are
  facts, not drafts to casually rewrite.
- The manifest artifact guide's schema, lint gates, and contract fields.
- Every flow, stable step ID, and explicit `action.kind` in the acceptance
  document. Assign implementation ownership only to `business-action` entries.
- During rework, `work show.context.review_state` and `required_closures`.
  Every closure is tied to a stable blocker and root cause; wording or node
  movement cannot close it by implication.

## Machine preflight and convergence mode

- Before Reviewer dispatch, OMAC machine preflight checks only mechanically
  certain facts that do not depend on project conventions, such as shell syntax
  and Go local package targets missing a `./` or `../` prefix. Without an
  explicit typed IO contract, OMAC does not infer artifact producers or input
  materialization from generic flag names, current file existence, or
  `scope_paths`.
- `work show.context.contract_boundary_schema` defines the optional typed IO
  shape. When a node needs an explicit artifact boundary, declare
  `evidence_mode: fixture|artifact|live`, stable
  `produces[].artifact_id` values, and `consumes[]` entries shaped as
  `{artifact_id, producer, evidence_mode}`. Fixture means the node owns complete
  executable deterministic fixtures; it does not mean waiting for downstream
  production artifacts.
- Every consume producer must exist, declare the artifact, and be a transitive
  upstream dependency. Do not invent `blocked_by` edges for coordination only.
  Legacy manifests that omit these optional fields keep their existing runtime
  semantics.
- A machine preflight failure returns directly to authoring without consuming a
  Reviewer cycle. `review_comment` contains only a bounded summary; the complete
  structured findings are stored in an attachment. Before reworking, run the
  summary's `omac work show <issue-id> --output json` command and read
  `context.machine_feedback`, then fix every finding before resubmitting.
- Acceptance responsibility is explicit structure, not an Action-prefix or node
  naming convention. The machine gate checks one full owner per flow, complete
  `business-action` coverage, and that the full owner transitively depends on
  every contribution owner.
- In `review_state.mode=normal`, close every `required_closures` item and review
  the full manifest impact.
- In `review_state.mode=convergence-audit`, stop patching findings one at a
  time. Group history by `root_cause_key`, audit the complete ownership,
  artifact, execution, and evidence chain, then repair every affected node and
  verification command together.
- Preserve blocker identity and provide independently reproducible closure
  evidence.

## Steps

1. Read `work show`, the design, acceptance document, references, current
   manifest or failure notes, and `submit`. For every source marked
   `content_externalized: true`, run the exact `omac work read` command and read
   the output file before decomposition.
2. Identify Wave 0 foundations: shared contracts, migrations, test
   infrastructure, CI gates, and independently acceptable foundation
   capabilities. Only complete capabilities directly consumed by later nodes
   are hard prerequisites. Do not create directory shells, fixed return values,
   placeholders, or production synthetic-data fallbacks.
3. Split Wave 1 into tracks along stable contracts and APIs to maximize parallel
   work. Within a track, schedule only the small foundation needed before the
   business module.
4. Make every node the smallest independently developable, testable, PR-able,
   and reviewable unit. If another capability can still form an independent
   PR/test/review boundary, split further.
5. Separate UI engine and interaction, API and UI, read model and write
   transaction, or backend capability and frontend display whenever stable
   contracts permit. Stop only when another split loses independent acceptance,
   breaks one transaction boundary, or creates unavoidable conflict.
6. Reserve Wave 2 integration acceptance nodes for cross-track critical paths
   and acceptance flows.
7. Put only genuine runtime prerequisites in `blocked_by`; put coordination-only
   dependencies in the description.
8. Build a global responsibility matrix from the authoritative acceptance
   content rather than node-name guesses. Give `acceptance_claims` to the unique
   integration/closeout node that executes the full flow without repeating its
   Action IDs; assign exact `{flow_id, action_ids}` for `business-action` entries
   through `acceptance_contributions`; use
   `acceptance_refs` for traceability that creates no acceptance obligation.
   Then complete `objective`, `source_of_truth`, `non_goals`,
   `verification_commands`, `integration_gates`, and `pr_base`.
9. Treat `scope_paths` as primary code ownership, not a file whitelist. Workers
   may change supporting files needed by the contract and explain them in the PR
   or verification. `non_goals`, contracts, verification, and review enforce
   the real boundary.
10. Write for low-reasoning-budget workers: objectives are deliverable outcomes;
    `source_of_truth` points to granular data and edge-case sections; non-goals
    name adjacent modules, legacy behavior, and forbidden refactors; verification
    commands and integration gates run as written.
11. For incremental work, add only nodes covering failed flows. Do not duplicate
    or rewrite completed nodes. Run manifest lint and, where needed,
    `omac dag check <manifest>` before submission.

## Completion conditions

- Wave 0, Wave 1, and Wave 2 responsibilities are clear. No independently
  PR/test/reviewable capability is merged without reason.
- `blocked_by` contains real prerequisites only; parallel nodes are not serialized
  for convenience.
- Every contract is complete and traceable to design anchors and acceptance
  flows; `pr_base` and verification entry points are explicit.
- Every flow has one full owner, every business Action has a contribution owner,
  and the full owner is or transitively depends on all contribution owners; no
  missing, duplicate, or upstream-impossible claim remains.
- Every node is a complete, production-usable, independently acceptable delivery
  within its own contract and does not need a later patch to acquire its claimed value.
- `scope_paths` communicates ownership, not a precise file list.
- Low-reasoning-budget workers can execute without inventing hidden context.
- The full or incremental manifest passes its required lint gate.

## Rework

Re-read the current task and `previous_review` or acceptance notes. Split nodes
that remain too coarse; move soft dependencies out of `blocked_by`; preserve
the original manifest and completed nodes after final-acceptance failure; rerun
lint and submit with the current command.

## Block and escalate

### Acceptance-responsibility amendment

- A global responsibility migration must use the compact `update-responsibility`
  operation: carry only `acceptance_claims`, `acceptance_contributions`,
  `acceptance_refs`, `clear_legacy_acceptance: true`, and named gate
  `acceptance_refs` patches. The current manifest is the sole source for every
  other contract field; never repeat a complete contract.
- The operation cannot carry or change objectives, sources, commands, scope,
  workers, `blocked_by`, topology, or runtime facts. A done/merged node allows
  only an acceptance-only `historical_contract_correction: true` with a reason;
  it never replays authoring/review/merge or dispatches an Agent. An unstarted
  node without a work item changes definition only when it omits `resume_stage`;
  an existing delivery returns to review by default.
- Any explicit `resume_stage: review|authoring|merging` requires an existing work item.
  To override the default recovery classification, put it on the same
  `update-responsibility` operation. `merging` requires an existing Reviewer-pass
  PR; accept silently syncs the new contract without resetting review, changing
  Store status/phase, dispatching an Agent, observing, or requesting a merge.
  The later `dag run` owns merge delivery. Never split one node into an
  `update-responsibility` plus a second `resume` operation: multiple operations
  for one node are invalid. A historical contract correction cannot set
  `resume_stage`.
- Reviewer receives the complete before/after responsibility matrix and historical
  correction audit through the obligation attachment returned by
  `omac work show ... --output json`; do not inline the large matrix in issue body.

Escalate conflicting design, acceptance, or ownership facts; missing reference
anchors, flows, `pr_base`, or verification entry points; ambiguous hard versus
soft dependencies that would affect parallelism; or an incremental fix that
needs a product-scope or shared-contract change. Report nodes, affected flows,
options, and risks before continuing.

## Prohibitions

- Do not implement product code.
- Do not turn soft dependencies into `blocked_by` or serialize every node.
- Do not copy design prose into descriptions; `source_of_truth` references stable
  anchors.
- Do not create mechanical microtasks without independent acceptance value.
- Do not create "build the skeleton first," temporary synthetic-data, or other
  nodes that only become valid after later work. Test doubles are never a
  production fallback.
- Do not make `scope_paths` a guessed exhaustive file list.
- Do not rewrite completed nodes during incremental decomposition or override
  instance failure facts with static guidance.

## Wrong → right

- Wrong: put API, UI, transactions, and integration testing in one node. Right:
  split Wave 1 at stable contracts and close with Wave 2.
- Wrong: list every related node in `blocked_by`. Right: list only unavoidable
  runtime prerequisites.
- Wrong: enumerate locks, generated files, and all possible edits in
  `scope_paths`. Right: state primary ownership and let review judge supporting
  files against the contract.
- Wrong: rewrite the DAG after acceptance fails. Right: preserve completed facts
  and add fix nodes for failed flows.

## Submit

Use `omac work submit <issue-id> --manifest-file <feature.yaml>`. Incremental
files contain only new fix nodes; OMAC validates and merges them into the
existing manifest.

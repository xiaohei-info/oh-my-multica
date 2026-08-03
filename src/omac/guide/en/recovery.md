# Exit 20 recovery protocol (Controller Agent)

When `omac dag run` returns exit 20, the deterministic engine needs a caller
decision. This is neither success nor an ordinary error to retry silently. The
structured stdout report is the current recovery fact.

## Authority order

1. The exit 20 report and `omac dag status <manifest> --output json`.
2. The node evidence chain from `omac node show <manifest> <key>`.
3. If a node has an issue, its `omac work show <issue-id> --output json` context.
4. Manifest contract and previous review.
5. This recovery guide.

## Decision flow

1. Run `omac dag status <manifest> --output json` for the complete snapshot.
2. For every unresolved node, run `omac node show <manifest> <key>` and read
   verification output, reviewer report, PR, platform issue link, and bounce count.
3. Choose an explicit action:
   - `omac node retry <manifest> <key> [--worker <replacement>]`: reset to todo.
   - `omac node accept <manifest> <key>`: accept a known risk and mark done.
   - `omac node abandon <manifest> <key>`: abandon the node and unlock downstream
     work that does not hard-depend on its deliverable.
   - Before execution starts, change the manifest and run `omac dag check`.
   - After execution starts, use controlled `omac dag amend propose` for contract,
     acceptance-mapping, or topology defects. Do not overwrite the live manifest.
4. Re-run `omac dag run <manifest>`. Completed nodes are reused; the remainder
   continues from current state.

### Stage-aware recovery and merge observation

- Recovery follows the issue's real `phase`: authoring resumes only the worker.
  If a reviewer run fails or finishes without submitting a verdict, OMAC keeps
  the same issue, worker PR/verification, and review subject, then redispatches
  the reviewer in `review`; it must not incorrectly return to the worker.
- `merging` only observes a persisted merge intent/request. GitHub/platform
  `UNKNOWN` results and temporary observation failures keep the node in
  `merging`; they do not consume `retry.merge`, return to the worker, or send a
  second merge request.
- Authentication or authorization failure is not a transient observation. OMAC
  durably marks the node `blocked` while preserving the work item, PR, and merge
  marker; it never reissues the merge. Restore credentials/permission, verify
  the PR remotely, then use the prompted explicit recovery command before
  rerunning the DAG.
- Only an explicit `CLOSED_UNMERGED` observation or a known merge-command
  failure enters merge-failure/rework semantics. `MERGED + mergedAt` remains
  the sole fact that closes a node.

## Continuing an exhausted plan-stage review

When `omac plan create/resume` returns exit 20 because plan, acceptance, or
decompose review rounds are exhausted, read `item_id`, `rounds`, `last_opinion`,
and `next_action`. A Human or authorized operator can then grant exactly one
additional round:

```bash
omac plan continue-review --dag-key decompose-p-xxxx \
  --reason "Human approved one additional review round"
omac plan resume --plan-id p-xxxx
```

- `continue-review` stores a small `review_continuation` decision on the same
  work item. It increases the absolute limit by one, never resets
  `review_bounce`, and refuses to stack another decision before the current one
  is consumed.
- When the review or machine-guard budget is exhausted, OMAC projects the same
  issue as `status=blocked`, `phase=review`, with bounded
  `omac.decision-required/v1` metadata containing the gate, round count, resume
  issue ID, and available evidence references. Complete findings remain in the
  review or machine-feedback attachments instead of being copied into metadata.
- An exhausted reject is restored through OMAC `reset_review` and todo status so
  the producer revises first. A final pass-with-nits delivery that was already
  revised proceeds directly to its next Reviewer round.
- The command does not modify `.omac/config.yaml` or `retry.review`, so a
  one-off operator decision cannot change the reviewed Git revision.
  `retry.review` remains the default budget for new work and legacy automation.
- The command performs a read-only active Agent check. If a run is active it
  refuses the decision and never cancels that run automatically.

## Choosing an action

| Signal | Inspect first | Usual action |
|---|---|---|
| `reviewer reject` | `report.blockers`, real diff, failed commands | Repair the node, then `omac node retry` |
| `contract-boundary-conflict` | `decision_required.conflict_codes`, review-report reference, current contract `responsibility` | If the Reviewer crossed the boundary, preserve the contract, record the corrected fact, and `omac node retry` the same node. If the contract truly lacks an upstream input, amend it first and resume the same issue/PR at the approved stage. |
| CI failure | CI log, `verification.commands` | Repair CI and retry; repair the contract or split if it is unsound |
| Merge retries exhausted | PR base, conflict files, integration branch | Reassign and retry, or resolve the conflict then rerun |
| `acceptance.max_rounds` exhausted | Failed-flow list, incremental manifest | Reduce scope, add nodes, or explicitly accept/abandon |

`accept` accepts a known risk; it does not skip failed verification. `retry`
requires new evidence or a new plan, not the same failed attempt.

## Controlled amendment of a running DAG

If a contract, acceptance responsibility, or dependency defect appears only after
an approved DAG starts running, do not rerun the whole plan or edit the manifest
by hand. Prepare the Reviewer/blocker report and pass the authoritative design
document paths:

```bash
omac dag amend propose .omac/project.yaml \
  --blocked-node bootstrap-console \
  --report-file /tmp/dag-review.md \
  --docs docs
```

- The Orchestrator submits only structured `omac.dag-amendment/v1` operations;
  runtime fields are not patchable.
- A global acceptance-responsibility migration must use `update-responsibility`:
  carry only the three responsibility fields, `clear_legacy_acceptance: true`, and
  named gate `acceptance_refs` patches, never a complete contract. Done/merged
  nodes remain immutable except an acceptance-only
  `historical_contract_correction: true` with an operation reason. That path writes
  only the manifest and a `historical_contract_correction/synced` ledger entry; it
  reads Store evidence only for pre-apply CAS and writes no contract, contract_ref,
  or other Store fact. It never recovers Store stages, dispatches an Agent, or
  replays a merge.
- Omitting `resume_stage` for an unstarted node without a work item preserves
  definition-only behavior. Any explicit `resume_stage: review|authoring|merging`
  requires an existing work item; for an existing work item, omission preserves
  the old minimal review recovery. Put an explicit stage on the same
  `update-responsibility` operation; never add a second `resume` operation for
  that node. `merging` requires a Reviewer-pass PR: accept silently
  syncs the new contract/contract_ref while preserving the review verdict,
  PR/verification, Store status/phase, and assignments. It dispatches no Agent
  and neither observes nor requests a merge; the later `dag run` owns that work.
  Historical contract correction cannot set `resume_stage`.
- OMAC checks DAG cycles, dependencies, the agent pool, immutable done/merged
  facts, and explicit ownership migration before independent Reviewer review.
- Reviewer pass returns exit 20 in `confirmation`; it never applies automatically.
  Inspect the generated amendment and run the returned
  `omac dag amend accept ...` command.
- Exhausted amendment review or machine-guard budgets are not confirmation. The
  issue remains at `blocked/review` with `decision_required`; after an explicit
  decision to continue, rerun the original `omac dag amend propose ...` command
  with `--resume-issue-id <issue-id>` to preserve the same issue, delivery, and
  Reviewer history.
- Plain `--resume-issue-id` preserves a valid Reviewer-pass confirmation and
  creates no new Agent Run. Resume first rereads current Store facts; any read
  failure fails closed as-is before contract/metadata writes, Runtime observation,
  assign, or wake. A caller snapshot never authorizes refresh or phase progress.
  Refresh is allowed only after a successful current read proving
  `TODO + authoring`, no deliverable/deliverable ref, and no stopped signal.
  A confirmation is consumable only when its pass/pass-with-nits verdict, current
  delivery subject, report, and evidence all revalidate. Otherwise OMAC exits 20
  without clearing confirmation or dispatching a Worker/Reviewer. If an amendment
  authoring Run stopped while Store already contains a delivery, OMAC likewise exits
  20 before Runtime observation, assign, or wake and directs the operator to preserve
  the old issue and create `--new-attempt --supersedes-issue-id <old-issue-id>`.
  No current engine exposes a real atomic conditional
  restart/dispatch API. `--restart-authoring` remains only as a compatibility
  entry point: it fails closed with exit 20 before any issue read, write, or
  Agent dispatch and returns a `--new-attempt` command. OMAC keeps no speculative
  generation/journal state machine. A new attempt preserves the old confirmation as
  audit history:

  ```bash
  omac dag amend propose .omac/project.yaml \
    --blocked-node bootstrap-console \
    --report-file /tmp/new-dag-review.md \
    --docs docs \
    --new-attempt \
    --supersedes-issue-id <old-issue-id>
  ```

  The attempt identity binds the manifest, report digest, recursive docs-content
  digest, blocked nodes, and superseded issue. Docs logical paths are relative to
  the manifest project root (the parent of `.omac/` when the manifest lives there),
  never the current working directory; docs outside that project fail closed. A
  crash retry reuses and finalizes the same issue only while it remains an
  undispatched `TODO + authoring` shell with no delivery/review evidence and no
  active Run. Once the attempt was dispatched, entered review/confirmation/a
  terminal status, or contains delivery/review evidence, another `--new-attempt`
  exits 20 and directs the operator to
  `omac work show <issue-id> --output json`. Continue such work through its normal
  current-phase command with `--resume-issue-id`; `--new-attempt` is not a resume
  operation. A different report or docs-content digest creates a different attempt.
  Metadata and source refs record the
  superseded issue, attempt id, report digest, and docs digest. The old issue is never
  cleared, reopened, or automatically closed. The new issue follows the normal
  authoring → Reviewer → human-confirmation flow and still targets the original
  manifest and nodes.
- `omac dag run`, `dag tick`, `dag amend propose`, and `dag amend accept`
  share one host-local lock for the same real manifest path. The CLI acquires it
  before engine construction or any Store/Runtime call, so a second OMAC process
  on the same host fails closed before creating an issue or Agent Run. This is
  deliberately not a distributed lock and does not turn Multica LWW metadata into
  conditional CAS. OMAC on another host, direct Multica/API writes, and other
  external actors are an unknown/unsupported concurrency boundary. Before the
  first dispatch OMAC still observes active Runs and rereads the pristine shell to
  reject conflicts that are already visible, but it does not claim linearizability
  and never cancels, clears, or compensates facts whose ownership cannot be proven.
- Accept runs under the manifest write lock with CAS and atomically writes the
  manifest definition plus a per-node apply ledger. The Store and filesystem do
  not share a transaction, so this is not a cross-system atomic transaction.
  Ledger states `pending`, `syncing`, `synced`, and `observed_progress` make the
  Store side effects restart-safe: repeated accept compensates only unfinished
  safe work. Historical correction entries start as
  `synced/store_side_effect:none`; accept never rolls back a node that already advanced.
- While any ledger entry is `pending`, `syncing`, or otherwise non-terminal,
  `dag run/tick/reconcile` fails closed before Store reads, dispatch, or merge.
  The evidence names the amendment identity, unfinished nodes, and the exact
  `omac dag amend accept <manifest> <amendment-file>` command for resuming that
  same human-confirmed amendment. Runner progress resumes only after every entry
  reaches `synced` or `observed_progress`.
- Runtime-only status or work-item changes are rebased only when the
  definition digest and minimum recovery set remain unchanged. Node, contract, edge, or
  affected-set drift requires a new reviewed amendment.
- A contract update is a complete replacement serialized through the canonical
  manifest serializer. Preserve only the typed boundary fields actually present
  in the existing contract. An omitted `consumes` must remain omitted unless the
  amendment explicitly changes the input policy. To clear the whole typed
  boundary, set top-level `clear_contract_boundary: true` and omit every
  boundary field from the replacement.
  `acceptance_claims`, `acceptance_contributions`, and
  `acceptance_refs` are preserved and validated against the authoritative file
  named by `meta.acceptance_file`. Acceptance drift after review rejects the
  first apply; once a pending ledger exists, crash recovery still completes the
  same amendment identity so the DAG cannot deadlock in a half-applied state.
- Unchanged nodes preserve work-item IDs, status, bounces, PR, verification/review
  references, and merged facts. Contract-only changes with unchanged delivery
  evidence resume at review; a valid passed-review PR may resume at merging;
  implementation-scope changes resume at authoring. Merge-only accept neither
  observes nor requests a PR merge; the next DAG run delegates that work to
  `run_merge_delivery`, where transient `UNKNOWN` observations consume no merge
  retry and cannot issue a duplicate merge request.
- Done/merged nodes cannot be changed or removed. Changing worker or `scope_paths`
  on an executed node requires an explicit ownership migration and reason.
- For `blocked_by`, worker, scope, or other implementation-semantic changes,
  OMAC computes the affected successor closure. Unstarted successors remain
  naturally dependency-blocked; started successors enter the explicit authoring
  recovery set. Reaching a done/merged successor fails closed and requires an
  Orchestrator-authored compensating node instead of calling it unaffected.

After apply, resume the original workflow:

```bash
omac dag run .omac/project.yaml
```

If accept reports definition or delivery-evidence drift, do not force the patch;
propose and review it again from current facts. Use `--resume-issue-id` to continue
an existing amendment issue after a process interruption.

## Agent versus Human decisions

A Controller Agent may retry without changing goals, contracts, or risk
acceptance—for example, reassigning to a better worker, repairing from an
existing blocker, or splitting a coarse node into behaviorally equivalent nodes.

Ask a Human before accepting failed verification or risk; abandoning a
user-visible capability or incomplete downstream acceptance scope; deleting an
acceptance flow; relaxing non-goals, coverage, integration gates, or product
scope; choosing between options with different compatibility, cost, migration,
or security consequences; or acting without required credentials, authorization,
or business decisions.

The request must include unresolved nodes, failure facts, commands run, blocked
downstream nodes, options, risk per option, and a recommendation—not merely
“confirmation needed.”

## Abandon semantics

`abandon` is explicit: the node no longer advances, but an abandoned upstream
counts as a satisfied dependency. Downstream work that does not hard-depend on
its deliverable may enter the ready-node set in the next round.

- Downstream nodes continue without waiting for the abandoned deliverable.
- Reports mark descendants of abandoned nodes because acceptance scope may be
  incomplete.
- `omac node retry` can restore the node to todo if the decision is reversed.

Use it for low-value repeatedly failing optional capabilities or experimental
integrations whose remaining work can ship independently.

## Common exit reasons

- Insufficient evidence, reviewer rejection, or exhausted CI/merge fallback:
  the node is blocked or needs decision.
- `pass-with-nits` normally returns to the worker for suggestions without using
  a review bounce or entering needs-decision.
- Final acceptance still has failures after `acceptance.max_rounds`: the report
  retains the failure list.

The review ledger counts submitted semantic reviews only; provider capacity,
transport, and attachment-read retries do not consume review cycles.
`review_convergence_decision` is the single convergence authority: the same
blocker remaining `unchanged` through two rework cycles stops at cycle three;
three open responsibility dimensions stop from cycle three; a root cause first seen
after cycle five marks expanding scope; and cycle ten is an unconditional stop.
OMAC persists the `review-convergence-*` decision before another Worker
dispatch. The decision requests a task-boundary reconsideration; for develop
nodes the Orchestrator proposes a DAG amendment, while OMAC never rewrites the
DAG implicitly.

## Failure isolation

- Hard-dependent downstream nodes become blocked and are not dispatched.
- Independent branches continue; one failure does not stop all work.
- The Controller Agent may reassign, split into two or three smaller nodes,
  reduce scope, or accept partial failure.

```yaml
# A repeatedly failing node
nodes:
  jwt-service:
    worker: frontend-agent
    blocked_by: [oauth-setup]

# Reassign and split along independently verifiable boundaries
nodes:
  jwt-core:
    worker: backend-agent
    blocked_by: [oauth-setup]
  jwt-middleware:
    worker: frontend-agent
    blocked_by: [jwt-core]
```

## Completion conditions

- Every exit 20 node has an explicit decision and reason.
- A changed manifest passes `omac dag check`.
- `omac dag run` was started again, or a Human received a clear explanation for
  why it is not being resumed.
- Before reporting completion, inspect the manifest. Non-terminal nodes without
  an active `dag run` mean the workflow is not complete.

## Prohibitions

- Do not retry automatically.
- Do not bypass failure isolation to advance a blocked node.
- Do not accept or abandon from guesses before reading instance facts and evidence.
- Do not report exit 20 as success.
- Do not change `retry.review` merely to continue one exhausted plan review and
  thereby change the reviewed revision; use `omac plan continue-review`.

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

### `pass-with-nits` acceptance

A develop node with `pass-with-nits` is blocked for an explicit caller decision;
OMAC does not dispatch the Worker for another unchanged verification. Inspect the
sealed delivery and review report, then choose one path:

```bash
omac node accept-nits <manifest> <node_key>
# or reject the nits and return to authoring:
omac node retry <manifest> <node_key>
```

`accept-nits` requires the current sealed delivery, matching review subject and
report reference, and no active direct Run. It preserves the Reviewer verdict and
report, writes only a bounded `omac.review-nits-acceptance/v1` marker, clears the
caller decision, and restores `review/in_review`; the next `dag run` still observes
remote merge facts and does not mark the node done directly. Repeating the command
is safe. A normal `node retry` clears the marker and invalidates the old review
projection before authoring a new delivery. When review rework history exists,
retry also records the previous delivery's PR head as the baseline even if the
current verdict was cleared; the Worker must submit a new head and cannot bypass
the Reviewer with unchanged code. If the old head or delivery-causality facts
are missing, OMAC fails closed rather than guessing or routing a same-head
submission with only a new attachment to Reviewer. When the old review report or
ledger is available, retry carries its references and a bounded blocker summary
in `previous_review`; the Worker must address those blockers in the new delivery.
If a reject is known but no report, ledger, or blocker context can be recovered,
retry stops with exit 20 instead of consuming another Worker round.

### Amendment `pass-with-nits` acceptance

When a running-DAG amendment receives `pass-with-nits`, OMAC preserves the
Reviewer verdict; it never fabricates `pass`. The amendment stops at the
confirmation gate for an explicit operator decision. If an interruption left
no reviewed amendment YAML, materialize it from the same issue with
`--resume-issue-id`; this consumes the sealed Store delivery/verdict and does
not dispatch another Worker or Reviewer:

```bash
omac dag amend propose <manifest> \
  --report-file <original-review-report> \
  --docs <persisted-contract-source> \
  --blocked-node <node> \
  --resume-issue-id <amendment-issue-id> \
  --output-file <amendment-file>
```

`--docs` must match the issue contract's persisted `source_of_truth`; do not
also pass `--report-file` as a design document. OMAC fails closed on docs or
contract drift, preserving the original verdict instead of silently changing
the review authority. Once the file exists, explicitly accept and then apply:

```bash
# Record explicit operator acceptance only; do not apply the amendment
omac dag amend accept-nits <manifest> <amendment-file>
# Continue the official apply path
omac dag amend accept <manifest> <amendment-file>
```

The first command writes a bounded
`omac.amendment-review-nits-acceptance/v1` marker only when the current issue,
review subject, report reference, ledger reference, and complete deliverable
still match and no active/unknown direct Run exists. It does not change the
Reviewer verdict, write the manifest, or apply the amendment. Any subject,
report, ledger, or deliverable drift, or a running Run, fails closed. Repeating
the command is safe when the marker is bound to the exact same review; a
mismatched marker is never overwritten. Only the second `omac dag amend accept`
performs the normal amendment CAS/apply and per-node ledger compensation; it
revalidates the marker against current Store facts and cannot bypass the first
step. A normal develop node still uses the separate `omac node accept-nits`
path; the two markers are not interchangeable.

### Repairing contract commands after an applied amendment

If an old manifest load already expanded `${VAR:-}` runtime placeholders to
`""`, do not re-run `amend accept`, guess environment values, or edit live
metadata by hand. Use the same Reviewer-passed, already-applied v2 amendment
source for a definition-only repair:

```bash
omac dag amend repair-contract-commands .omac/project.yaml /tmp/applied.amendment.yaml
```

The command requires matching identity/base digests and apply-ledger contract
digests. It restores only provable empty-default placeholder differences in
`verification_commands` and `integration_gates[].commands`. It accepts
`add.value.contract` and `update.set.contract` entries carrying already-applied
`description`/`blocked_by`, but never replays those or other operations. A new
node without a WorkItem receives a definition-only manifest repair; no issue is
created. If the current contract already equals the approved contract with
no command damage, repair also skips Store publication/ref repair rather than
creating an attachment just because `contract_ref` is absent. It reads and
snapshots current WorkItem runtime facts first,
rejecting active/unknown Runs, platform assignments, unrelated contract drift,
and unproven Store outcomes. If an attachment was published before its response
was lost, only one existing publication with the OMAC contract producer marker,
the same issue binding, digest-bound filename, and exact digest/byte count may
be adopted; it never blindly publishes a duplicate. A durable receipt is written
before each Store/manifest side effect, so repeating the same command resumes
safely without changing status, phase, bounce, PR, verification, or review
facts. The command commits the manifest; a failed push remains a local commit
and follows the normal git-sync warning path.

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
- A Worker `omac work submit` that races the no-submit retry boundary is
  re-read and sealed by the Controller before the limit is consumed, including
  its `delivery_identity`. The no-submit limit still blocks when the final
  authoritative read proves that no fresh delivery exists.
- If a race leaves the WorkItem and manifest at `blocked/authoring` while the
  same causal `worker_handoff` remains and authoritative evidence proves a fresh
  delivery, the next `dag tick/run` restores the collect path, seals the delivery,
  and routes it to Reviewer. An existing `decision_required` or unproven delivery
  remains blocked; do not edit metadata manually.
- The complete convergence report and review ledger remain in their attachment
  references. The `decision_required` control projection stores only bounded
  routing fields, scalar audit facts, and count/digest summaries for long lists,
  while retaining `review_report_ref`, `review_ledger_ref`, and `contract_ref`.
  If that projection still cannot fit the platform metadata limit, OMAC fails
  closed rather than dropping facts or advancing the workflow.
- If an amendment Reviewer Run explicitly terminates without a verdict,
  `dag amend propose --resume-issue-id` clears only the matching
  `reviewer-completed-without-verdict` decision after proving no Run is active,
  then redispatches Reviewer on the same issue. Other decisions are never cleared.
- A `recovery_marker` keeps a node in the active control barrier even when its
  manifest projection says `done + merged + merged_at`. OMAC clears that marker
  only after an authoritative control read proves no handoff, review baseline,
  or decision remains; a failed or unavailable read stays fail-closed so a real
  recovery fact cannot be swallowed as terminal.

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

When `commit_manifest` cannot push, OMAC keeps the local manifest commit and
uses exponential backoff for that repository/path (10 seconds initially, capped
at 5 minutes). Calls inside the window do not issue another push or warning.
Remote lag remains an explicit sync warning rather than a false synced state;
a successful push clears the backoff.

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
  facts, and explicit ownership migration before independent Reviewer review. If
  a blocked node already has `decision_required`, amendment admission accepts
  only `review-convergence-*` or `contract-boundary-conflict`; no-submit, network,
  metadata, and Runner errors stay on their own recovery paths instead of being
  retried through an amendment.
- Reviewer pass returns exit 20 in `confirmation`; it never applies automatically.
  Inspect the generated amendment and run the returned
  `omac dag amend accept ...` command. New files use
  `identity_schema: omac.dag-amendment-identity/v2`, binding the base
  manifest/acceptance digests and review issue into the amendment identity. Older
  files without the marker remain readable through the legacy identity path; the
  next re-propose emits the v2 marker.
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
- An authoring recovery atomically switches the Store to a new
  `review_generation` and retires the prior current decision, verdict/report,
  continuation, and handoff. Historical review ledger references and absolute
  bounce audit counters remain intact. `work show` projects `review_state` and
  `required_closures` only when `review_ledger_generation` matches the current
  generation; an ordinary review rejection does not switch generations.
- An authoring entry marked `synced` by an older CLI but missing the generation
  projection is repaired idempotently by repeating the original
  `omac dag amend accept` command; no manual metadata edit is required. Repair
  still rejects active formal Runs and verifies the contract digest, generation,
  retired decision/report/subject/handoff, and current-ledger visibility before
  returning the entry to `synced`. If the WorkItem has sealed a new delivery
  identity, entered review, or switched to another generation, repeated accept
  records `observed_progress` without rolling back the progressed Store facts.
- Bounce fields remain monotonic absolute audit counters and are never reset.
  A review-reject Worker handoff also records the previously reviewed PR head;
  a fresh Run reporting the same reject head cannot go straight to Reviewer and
  follows the Worker retry/decision path. `pass-with-nits` and legacy unsealed
  evidence-only paths may still reuse a head with a new evidence attachment.
  `work show.task.bounce_budget` and Worker retry logs distinguish the absolute
  value, amendment baseline, and current-generation consumption. Runtime budget
  decisions continue to use the relative calculation from the manifest
  `amendment_apply.bounce_baseline` fact.
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
- `pass-with-nits` stops at caller acceptance; it does not dispatch the Worker
  again until the caller explicitly chooses `omac node retry`.
- Final acceptance still has failures after `acceptance.max_rounds`: the report
  retains the failure list.

The review ledger counts submitted semantic reviews only; provider capacity,
transport, and attachment-read retries do not consume review cycles.
`review_convergence_decision` is the single convergence authority: the same
blocker remaining `unchanged` through two rework cycles stops at cycle three;
`scope-expanding` additionally requires at least two consecutive non-reducing
blocker transitions, an explicit `owner` on every open blocker, and at least two
distinct owners. Three dimensions alone, a still-reducing blocker set, or missing
owner evidence does not admit an amendment. A root cause first seen after cycle
five follows the same admission rule; cycle ten remains an unconditional stop.
OMAC persists the `review-convergence-*` decision before another Worker dispatch.
The decision requests a task-boundary reconsideration; for develop nodes the
Orchestrator proposes a DAG amendment, while OMAC never rewrites the DAG implicitly.

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

## Historical reads, generation isolation, and prerequisite blockers

Completed `work show` exposes original ledgers and refs under
`context.review_history`, marked `unverified-history`. It does not reinterpret
historical rounds or certify active convergence. New ledgers use
`omac.review-ledger/v2` with ledger-local sequential rounds. Existing v1 must
pass the same strict validation before active reuse; retire incompatible history
through explicit authoring recovery, never by renumbering it.

`node retry --stage review` requires a complete controller-sealed delivery
identity matching current PR/verification refs and a timezone-aware verification
time. Otherwise run `omac node retry <manifest> <node> --stage authoring` and
obtain a fresh Worker submission. Never invent an identity or Reviewer Run
baseline. Amendment review recovery checks this before applying changes.

Recovered reports must match the current ledger generation, subject, and report
digest. Carried handoff feedback is bound to its target generation and contract.
Retired reviews remain audit evidence. For a contaminated Harness, confirm its
SDK-only manifest contract, perform authoring retry, then verify that `work show`
contains no retired Host-positive `previous_review` obligations.

For Audit upstream read exit 5, use the Worker guide's `work block` protocol.
After repair, verify `omac work show <upstream-id>`, then explicitly run
`omac node retry <manifest> <audit-node> --stage authoring`. Absolute counters
remain audit history. If the budget is exhausted, use the existing reviewed
amendment recovery/fresh-budget workflow; do not raise limits.

Run-list reads use bounded exponential backoff, including TLS handshake timeouts.
Certificate, authentication, permission, and unknown errors fail immediately;
exhausted reads retain exit 2. Inspect existing Runs before restarting one
supervised `omac dag run <manifest>` process. A Runner exit does not authorize
cancelling or redispatching already-running Agents.

### Same-head submission after authoring recovery

An ordinary authoring recovery creates an `explicit-dispatch` handoff without
source review verdict/feedback. Its verification attachment baseline distinguishes
new submissions; it does not require a new commit on unchanged code. The new
formal verification must still belong to the current Worker Run, and its submitted
HEAD must match the remote PR HEAD. Only then may the Controller seal the delivery
and enter review without consuming a no-submit retry.

Reject rework still requires changing the rejected HEAD. Missing reject baselines
and unknown `operator-retry` provenance remain fail-closed. Old verification,
missing HEAD, and unrelated Run attachments remain invalid. An existing operator
prerequisite decision is not automatically cleared by deployment, and neither
counters nor Runner state are changed. Preserve the submitted delivery and handoff
for explicit operator recovery.

### Preserve reject feedback across authoring handoff

Before clearing live review fields, an ordinary reject handoff retains bounded
blocker summaries and full report/ledger references, bound to the current contract
and review generation. `work show.context.previous_review` exposes this feedback;
no-submit retries and restart recovery preserve those obligations.

If an older version cleared both report and handoff, explicit authoring retry can
recover the last subject/report digest from a strictly validated current-generation
ledger and match its immutable attachment. A missing live subject does not mean
there was no review. Generation drift, invalid ledgers, and report digest mismatch
still fail closed; comment recency alone never selects feedback. Recovered handoffs
retain the original reject verdict and subject. Rejected-head change checks remain
unchanged. Read `previous_review` before proceeding; a successful earlier submission
is not review approval.

### Identity lookup before amendment or task creation

Multica `find_work_item_by_dag_key` uses the existing project-scoped paginated Issue
envelopes to match an exact `dag_key` or `[DAG:key]` title prefix, including shells
created before metadata was written. Only the first match is fully hydrated and
its attachments validated; unrelated attachments are not downloaded. An absent
identity returns none. Selected-item attachment failures and pagination errors
still propagate, rather than being treated as absence and creating a duplicate.

A Multica Issue envelope's `duplicate_of: null` is an empty default and does not
block idempotent recovery of an undispatched amendment shell. Only JSON null for
this named field is ignored; other values and arbitrary unknown fields still fail
closed. Recover the same attempt with unchanged manifest/report/docs/supersedes inputs and
the same deterministic dag_key, without creating a duplicate issue.

### Budget authority across consecutive amendments

A new amendment's `amendment_apply.nodes` contains only its own recovery work.
Completed budget authorizations for unaffected nodes are carried separately in
`amendment_apply.retained_bounce_baselines`, with the source amendment, work item
ID, contract digest, and original worker/review/merge baseline. Old recovery steps
are not replayed. A new recovery of the same node supersedes its older baseline;
removed nodes, changed issue/contract identities, and invalid baselines do not
inherit authority.

Budget decisions use accepted manifest authority, never an arbitrary Store
bounce_baseline projection. Absolute counters and configured limits are unchanged.
Already-lost baselines require verification against the original applied ledger
and Git history, then a narrowly scoped restoration under single-writer control.
Do not replay the old apply queue, raise limits, clear audit counters, or invent a
contract change to compensate for lost budget records.

### A Worker requests an operator decision

For an active develop/authoring execution, re-read
`omac work show <issue-id> --output json`, copy `control.blocker_report_template`,
and use its `report_blocker` command to submit an `omac.worker-blocker/v2` report.
This is for a contract stop condition or an owner decision, not ordinary Reviewer
reject rework. Completed old Runs cannot be reported retroactively.

Use `quality-gate-failed` with at least one actual command, nonzero integer
exit_code, retained evidence ref and observation. Use `owner-decision-required`
with evidence refs/observations and a specific decision_needed. contract_ref must
name an existing top-level context.contract field; explain its relevance in
summary. Do not run later verification forbidden by the stop condition, invent a
contract authorization, or claim local uncommitted files are independently
reproduced. Evidence is a claim to inspect, not a pass verdict.

Keep the issue_id, review_context_binding, handoff_generation, worker and current
direct Worker run_id from the template. These bind causality, not caller
credentials. If the target Run is not yet bound, wait and re-read work show; do
not substitute a session ID or an old Run.

Confirm exit 20, terminal=true and next_action=stop before stopping. An identical
report is idempotent, including recovery after an interrupted status write.
OMAC persists the decision before blocking; it preserves work, counters, budget
and review history and stops automatic redispatch of this node. Other independent
nodes can continue. Consumed budget is not refunded. The v1 upstream-unreadable
format remains supported. Resume only through an explicit operator retry or a
real amendment after resolving the decision.

Block, submit and Worker redispatch share a host-local lock and re-read control
before writes. This is not cross-host CAS: retain the single-Runner control
boundary and do not concurrently replace a node's generation from multiple hosts.

Reports are limited to 2048 UTF-8 JSON bytes and 1–4 evidence entries; keep full logs at the referenced location.

Before creating a Worker handoff, OMAC materializes a deferred Store contract
before hashing it. An unloaded `None` is not an authoritative null contract.
Attachment failure stops dispatch instead of persisting a null hash. Existing
bad bindings are not silently rewritten or accepted by `work block`; preserve
failure evidence and create a new handoff only through explicitly authorized
recovery.

Ordinary review rework materializes the source subject’s delivery and review
evidence after the locked control re-read, before exact subject validation.
Deferred bodies are not missing evidence; actual subject drift still fails before
writes. This read correction does not clear rejects, alter budgets or grant recovery.

After observing a stage-task Agent Run as terminal, OMAC re-reads the delivery or
Reviewer verdict before recording a missing-submission decision. A failed final
read preserves existing facts. Plan submissions use the plan/project-rules pair
and transition to review; they do not use develop's delivery_identity.

A current explicit-dispatch handoff does not make a retired review ledger current
again during `node retry`. Historical review counters remain audit facts; current
review evidence or a handoff carrying actual review rework still requires its
review context before recovery.

### Single-use same-HEAD evidence review

`omac node review-evidence` handles an existing evidence-only-rework-head-policy
block only. An explicitly approved original Agent session may witness the historical
handoff/baseline association. It does not replace independent platform Run,
attachment attribution, contract, HEAD, reject or artifact-byte verification.
Preserve the unedited JSONL and independently pin its SHA256; select the one-based
line containing a complete Issue JSON toolResult.

```bash
omac node review-evidence <manifest> <node> \
  --witness-file <original-session.jsonl> --witness-line <line> \
  --witness-sha256 <approved-file-sha256> \
  --reason '<explicit operator authorization>' > evidence-review-request.json
```

Preview is read-only. It freshly downloads old/new verification, source reject
report/ledger and every retrievable_artifact. Only same-PR-repository GitHub blob
URLs pinned to full commit SHAs are supported. Attachment task_id or parent
comment.source_task_id must identify the original completed Worker Run; conflicting
IDs are rejected. The uploader and time window must match. A later Run or the
historical baseline cannot stand in for that submission.

After auditing the exact request, the single controller may consume it:

```bash
omac node review-evidence <manifest> <node> \
  --witness-file <same-original-session.jsonl> \
  --apply-request evidence-review-request.json
```

All bindings and published bytes are checked again. The Controller generates the
identity through normal sealing; callers cannot supply it. The preserved source
reject ledger is bound to the verified current contract generation so its blockers
remain independent-review obligations. This never grants a verdict, resets counts
or budgets, or changes the normal same-HEAD reject policy.

Progress is recorded in manifest.meta.evidence_review_authorizations. Resume an
interrupted operation with the identical request and witness; completed consumption
cannot be replayed. Changed Runs, HEAD, contract/generation, attachments, artifact
set, control facts or amendment authority fail closed. A witness hash pins bytes,
not platform authenticity. No approved witness means no recovery from truncated
history.

Success means ready-for-independent-review only: no Agent is dispatched and no
node is marked done. The coordinator separately supervises ordinary `omac dag run`
after checking facts; that command can advance the whole DAG. Keep one controller:
host-local manifest/Store locks are not cross-host CAS. Do not manufacture source
commits or write sealed identities manually.

The supported witness profile pairs the toolResult with one original bash toolCall
whose exact command is `multica issue get <issue-id> --output json`; echoed or
transformed JSON is rejected. Older sealed identities without verification_task_id
remain valid only when newly exposed comment attribution equals their already
sealed run_id. Conflicting Runs are rejected and old identities are not rewritten.

## Exact contract literal correction

Generic non_goals changes remain implementation-affecting. The sole operation
`correct-contract-literal` changes one identifier occurrence in one non_goals
string, with identical full contracts otherwise. It is restricted to an existing
unmerged TODO/authoring target with no assignee, handoff, delivery or consumed
budget. It is not a general recovery exemption.

Use `omac dag amend prepare-literal-correction --help` for read-only preparation.
Supply the exact original/replacement token and non_goals index, a commit-pinned
GitHub docs URL, verified SHA256 and canonical quote, and the real rejected
amendment issue. The rejected proposal must name this same target and full
replacement contract. Preparation freezes its report/ledger and completed
independent Reviewer provenance, plus every target/descendant manifest, Store and
Run snapshot. Unknown/non-terminal Runs or any snapshot drift fail closed.

A target normally has no Run history. The sole exception is one completed formal
direct Run of the same Worker: complete original platform tool records must pair
the exact work-block command with an untruncated v2 owner-decision terminal
receipt (exit 20), bound to the issue, Run, Worker, full old-contract hash and both
literal tokens. No submission attempt or later tool call is permitted. The receipt
and complete message digest are frozen. Generic completed, failed/cancelled,
multiple, foreign or unproved Runs still fail closed. This historical evidence
does not restore a retired decision/handoff, erase history or grant a budget.

The coordinator submits the untouched prepared proposal through a new ordinary
amendment attempt. A dedicated review obligation requires independent authority
verification and an explicit proof of no semantic scope, quality, ownership,
output or downstream implementation effect. The original reject stays intact.
Only a new actual pass/confirmation, exact submitted proposal and report bytes,
and uniquely completed independent Reviewer Run may produce a reviewed envelope;
pass-with-nits is not accepted for this operation.

Human `dag amend accept` reuses amendment identity, definition CAS and the normal
restart-safe target authoring ledger. Descendants and absolute counters remain
unchanged. Targets with historical consumed budget are unsupported to avoid
creating a fresh allowance via an authoring baseline. Authority, reject, review or
runtime drift requires preparation and independent review again. Re-entry observes
the existing consumed ledger without dispatch. Retain the single-writer boundary;
this is not a cross-host platform transaction or a selected-DAG tick bypass.

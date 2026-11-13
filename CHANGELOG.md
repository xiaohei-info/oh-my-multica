# Changelog

This file records public changes to oh-my-multica. The format follows
[Keep a Changelog], and version numbers follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

### Fixed

- Paths and branch names in committed content: `pending_human_amendment.file`, the amendment apply ledger's `amendment_file`, the
  system-amendment intent's `file` and the config-sync commit subject / `config_synced` log no longer carry machine paths. They are
  repo-relative, or a bare file name for a file outside the repository (`core/pathrefs.py`); absolute records written by earlier
  versions are still read. The amendment authoring contract no longer uses the nonexistent branch `manifest-runtime` as `pr_base`:
  it takes the manifest's `meta.pr_base` (else the nodes' single `pr_base`, else `main`), so the Orchestrator can read the manifest.
- Upstream defects: a `queued` defect now records what it waits for (`waits_on`: `amendment:<id>`, `fix:<node>`) in the ledger.
  `defects.advance` compares the record with the manifest at the start of every tick (no platform read) and routes the report
  again as soon as the cause changes or goes away, independent of the process-local memory that only suppresses repeats; `omac
  defect list` shows the cause. A fix that finished is saved as `fixed` together with the rest of the pass.
- Upstream defects: `defect-budget-exhausted` now says which budget is spent. The decision carries `budget: {layers, reporter:
  {used, limit}, global: {used, limit}}` and one copyable `omac defect grant` per spent layer. New `omac defect grant --global
  [--count N]` raises the DAG-wide `defects.max_auto_fixes` (ledger `granted_global`) and re-queues every report the global limit
  stopped; `--reporter` and `--global` are mutually exclusive. The `reason_code` is unchanged.
- A Worker or Reviewer Run that failed on provider quota or rate limit (Multica `failure_reason` `agent_error.provider_capacity_or_rate_limit`,
  or a `429` / "hit your … limit" message) was judged non-retryable and asked a person to decide. Run failures now share one classification
  (`capacity` / `transient` / `nonretryable`); capacity failures are waited out until the reset time in the error (or a bounded backoff), then
  rerun, with no bounce, audit round or Run budget spent and without holding other nodes. `retry.capacity_wait_hours` (default 12) bounds the wait;
  past it the node stops as `capacity-wait-exhausted`.
- After a CI bounce of the rework, `omac node retry` measured the Worker's next submit against the head of that (never reviewed) delivery,
  so a Worker that had nothing left to change submitted successfully, was counted as "finished without submit" and exhausted `retry.worker`. The
  handoff now records `source_review_superseded` and then only requires a fresh verification attachment.

### Fixed

- `omac dag run` waited for nothing between two ticks, so a DAG that only waited for Agents re-read the platform as fast as a tick
  could run. A tick that changed no node status and dispatched nothing is now followed by a wait of `defaults.poll_interval`
  (default 30 s); a tick that moved something is followed at once, and a stop signal ends the wait. The final-acceptance loop
  used `poll_interval` with default 0; it now uses the same default.
- Upstream defects: a fix node no longer copies the reporter's `repro` command into its `verification_commands`. That command
  usually runs tests that exist only on the reporter's unmerged branch, so the fix node could never pass and the reporter waited
  on it forever. The command stays in the fix node's description; the reporter re-runs it after the fix merges, and the same
  fingerprint appearing again stops as `defect-recurred`.

### Fixed

- A defect report that only waits (`queued`: a fix of the same node is in flight, or a person's amendment is pending) is no
  longer routed, logged (`defect_routed`) and re-read from the platform on every tick. `defects.advance` remembers what the
  wait depends on (fix states, pending reports, human amendment, defect config) and skips the pass until that changes.
- A node-level delivery or control inconsistency no longer makes `omac dag run` exit. Such failures raise
  `errors.NodeFailure` (a `PlatformError` subclass, so an uncaught one is still exit 2) and the Loop settles them per node:
  the node stops with a `decision_required` carrying a `reason_code` and a copyable `omac node retry`, and every other node
  keeps advancing. Platform outages, authentication, manifest, lock and observation-snapshot errors still stop the run.
- A PR HEAD that moved after the Worker's submit (`delivery-head-moved`) voids that delivery instead of stopping the run: the
  node returns to its Worker through a new handoff generation with a note on the issue, spending one `retry.worker`; with the
  budget used up, or once a merge has started, the node stops with a decision. A push during rework is never compared with
  the previous submit. The Worker protocol and guide now say not to push to the PR branch after submitting.
- The restore pass for stopped nodes no longer fails the whole tick on one node's invalid structured payload or review
  ledger: that node stays stopped and the others are restored.

## [3.0.7] — 2026-11-10

### Fixed

- A Worker blocker (`owner-decision-required`) can now be answered: `omac node retry <manifest> <node> --answer "<text>"` binds the
  answer to the blocker's `handoff_generation`, stores it on the work item, exposes it as `omac work show` `control.owner_answer`,
  and posts it as an issue comment so a resumed Worker session sees it. The Worker guide says to execute the answer instead of
  re-blocking for lack of a new commit or fix node.

## [3.0.6] — 2026-11-09

### Fixed

- Upstream defects: a new fingerprint is rerun against a merged fix of the same node only when that fix was merged after the
  reporter's newest Run started; an older fix gets a new fix node instead. `defects.per_suspected` (default 2) stops further
  automatic fixes of one node (`defect-node-fix-cap`); `omac defect resolve` records a fix merged outside OMAC (#180).

### CI

- The Docker job's Multica CLI and `checksums.txt` downloads retry (`--retry 5 --retry-delay 10 --retry-all-errors`) so intermittent
  GitHub Release 5xx errors no longer fail the shard.

## [3.0.5] — 2026-11-09

### Fixed

- **Every metadata write now shares one 8KB budget, and a Reviewer submit the platform rejects is a named, recoverable block.**
  When the platform rejects a write at the limit (any key, including `review_report_ref` / `review_ledger_ref` /
  `verification_ref`), `MulticaStore._free_metadata_budget` first applies the recoverable reductions, then moves inline
  `delivery_identity`, `reviewer_run_baseline` and `worker_handoff` to immutable attachments (same reference mechanism as the
  handoff; the one reader is `_json_control_metadata`). Still no room: `MetadataBudgetError` (`platform-metadata-budget`), nothing
  silently dropped. `omac work submit` records that rejection as a comment (`core/submit_rejection.py`); the Loop reads it
  (`WorkItemStore.observe_submit_rejection`) and blocks with `reviewer-submit-rejected-by-platform` instead of spending the
  no-submit budget; the printed `omac node retry <manifest> <node> --stage review` clears it.

## [3.0.4] — 2026-11-09

### Fixed

- **A Reviewer's `omac work submit` no longer fails on Multica's 8KB issue-metadata limit.** `source_refs` of a develop issue grows
  with its upstream list and is rewritten on every dispatch, so a node with many upstreams was already near the limit before the
  verdict was written. The shrink of recoverable fields (`source_refs`, `reviewer_run_baseline` run ids, inline
  `review_obligations`) used to run only when a decision was written. `MulticaStore._set_metadata` now runs it when the platform
  rejects any write at the limit, then retries once; the full values stay recoverable from the issue body and attachments. A
  failed submit no longer leaves a duplicate report attachment per retry.
- **A node blocked only because its upstream failed returns to `todo` once the upstream is satisfied, even when it already has a
  work item** (a reporter that waited for a defect fix). `loop._maybe_unblock` unblocks it when its platform issue is `todo`
  with no standing decision; a node that failed itself still needs `omac node retry`.
- **A defect whose fix node is done becomes `fixed` in the ledger even when no report is pending** (`omac defect list` showed
  `fixing` forever).

## [3.0.3] — 2026-11-08

### Fixed

- **`omac node retry --stage review` now works on the block the Loop itself writes when a Reviewer finishes without submitting.** The
  `reviewer-run-no-submit-retry-exhausted` and `reviewer-run-terminal-time-unavailable` decisions carry a Run id, which the
  recovery classifier did not recognise, so the advertised `next_action` was refused ("the persisted recovery decision does not
  identify this review"). They now reset the review stage like the other explicit Reviewer retries.

## [3.0.2] — 2026-11-08

### Fixed

- **A person's defect report for a node blocked on another worker decision now gets its fix node.** The system amendment
  refused such a reporter ("holds an unresolved worker decision"), the report ended as `defect-unroutable`, and the reason
  was dropped. A system amendment now supersedes the decision of the nodes it recovers.
- **A stopped defect keeps its reason.** `defect-unroutable` and every other stop write `stop.reason_code` and
  `stop.detail` on the ledger entry (`omac defect list`) and, for a blocked reporter, into its `decision_required`.
- **A stopped person's report is logged once.** A report stopped by `defect-budget-exhausted` was re-routed and logged
  twice per tick (about 2,500 lines in two minutes). It is marked stopped; `omac defect grant` or a new `defect report`
  queues it again.

## [3.0.1] — 2026-11-08

### Fixed

- **A tick that changes nothing no longer commits.** The reconcile-audit schedule (`completed_interval_ticks`,
  `last_full_scan_at`) changed on every tick and was committed and pushed as `chore(omac): manifest sync`, thousands of
  times a day. It is now host-local runtime data outside git; a lost file forces one early full scan. A running DAG reads
  as before: the old `reconcile_audit` field is dropped on load and the next save removes it from git once.
- **A stuck git sync stops the loop.** When git refused to start the rebase (uncommitted changes), OMAC still ran
  `rebase --abort`, logged `no rebase in progress` and kept ticking with nothing pushed. Sync failures now name their step
  (`rebase_not_started`, `rebase_conflict`, `rebase_abort`, `push`, `push_retry`, ...); a rebase that never started is
  not aborted, and when the same step keeps failing until the push backoff reaches its 5-minute cap, `dag run` exits 20
  with git's error and repair commands.

## [3.0.0] — 2026-11-06

A major release. Exit codes, existing `--output json` fields (only additions), the three ports and the `ignore_imports`
baseline are unchanged. Read "Migrating from 2.4.x" below before upgrading a running DAG.

### Breaking

- **Mixed versions are not supported.** A 3.0 manifest can carry fields (`provides`/`requires`/`registers`/`requirement_refs`/
  `defect_refs`, acceptance `requirements`) that a 2.x reader silently drops. A new plan writes `meta.omac_schema: 3`;
  omac refuses a manifest whose `omac_schema` is newer than the release and says how to upgrade.
- **A proposal reviewed under 2.x and not yet accepted is no longer valid.** The definition digest now leaves out all runtime
  `meta` keys, so it differs from the 2.x one; run `omac dag amend propose` again.
- **New plans are checked harder** (`standard` profile, below) and every node with `acceptance_refs` gets one more review
  obligation (`trace:refs`).
- **The DAG can change by itself.** `defects.auto_fix` defaults to `design-gap`: a reported upstream defect inserts a fix
  node through a code-built system amendment. Set `defects.auto_fix: off` to keep every such report as an exit-20 decision.
- **Final acceptance** merges the increment of a failed flow through a system amendment, and a flow that fails again after its
  increment was merged stops the loop (it used to run the next round).
- **The Reviewer of a `contract.evidence_mode: live` node judges the Worker's live evidence** (command, exit code, artifacts)
  and the PR diff; it no longer reruns commands that need the live environment.

### State

- New `.omac/<name>.state/_dag.yaml` holds DAG-level runtime facts: `reconcile_audit` (migrated out of `meta` on load; the next
  save rewrites the definition file once), the defect ledger, `pending_human_amendment`, `auto_held`. `SetDagFact` is the
  effect that writes it. A running `dag run` reloads a definition file someone else changed at the start of every tick, so a
  save never puts a stale definition back (for example over `node retry --worker`).
- Node state files gain `retry_note` and `defect_pending`.

### Planning

- Acceptance `omac.acceptance/v3`: `requirements` (id, source, summary, disposition, verification `action|technical`) and
  `action.requirement_ids`. The acceptance guard checks ids and coverage; decompose must give every `technical` requirement a node
  with `contract.requirement_refs`. Reviewers get `requirements:extraction` and `interface:matrix` obligations.
- Typed interfaces: artifacts declare `provides: [{id, ref}]` and `requires: [id]`; `meta.registries` and node `registers`
  name a guard node that must run after every registrant. `lint` and `lint_increment` report a missing provide or a misplaced
  guard with a YAML snippet to paste. Reviewers get `interface:<artifact>:<id>`, `registry:<id>` and `trace:refs`; a `pass`
  for these must cite a path with an extension or a test name.
- Plan profile `light` / `standard`, decided by code from the node and business-action counts (`plan.light_max_nodes` 12,
  `plan.light_max_business_actions` 20; only `plan.profile` overrides it), recorded as `meta.plan_scale`. `light` adds no gate
  to the 2.4 plan.
- A plan stage stops when a machine-gate round does not lower the number of errors (`guard-non-reducing`) and when a Reviewer's
  report fails the evidence gate more than `review.invalid_evidence_restarts` (2) times on one subject
  (`review-evidence-invalid`); `plan resume` releases both.
- Ready nodes are dispatched by priority: owner of a complete flow, then most transitive downstream nodes, then manifest order.

### Upstream defect feedback

- `omac.worker-blocker/v3` adds the reasons `upstream-defect` (`defects`, 1–6 items; v2 reports are still accepted) and
  `environment-failure`. A Reviewer whose every open blocker names a finished upstream node as `owner` produces the same
  defect report instead of rework, without spending a review bounce.
- Between two ticks of `dag run|tick`, `pipeline/defects.py` fingerprints each defect and routes it with one table
  (`decide_defects.DEFECT_ROUTES`): stop for a person (`defect-needs-owner`, `-unroutable`, `-fix-generation-cap`, `-recurred`,
  `-budget-exhausted`, each with copyable commands), queue, attach to a fix in flight, or insert a `fix.*` node. The reporter
  waits for the fix through `blocked_by`. Intent → system-amendment file → `apply_amendment` → `fixing`; a restart finishes or
  withdraws the intent.
- New `omac defect list|report|grant`. New config `defects.auto_fix|per_reporter|max_auto_fixes`.

### Running a DAG

- `omac dag run --wait-decisions [--max-minutes N]`, read-only `omac dag decisions` (stable `decision_key`) and
  `omac dag followups`; the closeout node's review gains `followups:triage`.
- `environment-failure` is retried once by the same Worker (`retry.environment`, default 1, `bounces.environment`), then exit 20
  with `omac node retry … --worker <next worker>`.
- `dispatch.hold_live` (default false) holds each ready `evidence_mode: live` node once until `omac node release`.
- `omac node retry --note <text>` (at most 2000 characters) puts an operator note into the Worker's issue body for that retry.

### Fixes

- `omac node accept` and `omac node abandon` retire the `decision_required` and `worker_handoff` they answer (see 2.4.1).
- `reconcile_with_observations` kept a stale on-disk baseline after replacing `meta`/nodes, so the next save rewrote more files than needed.

### Migrating from 2.4.x

1. Stop running `dag run` processes, upgrade omac on **every** machine that touches the manifest (`pipx install … @v3.0.0`),
   then restart. Do not mix 2.x and 3.0 on one manifest.
2. An existing manifest keeps working: `meta.reconcile_audit` moves into `_dag.yaml` on first load, and the first save rewrites
   the definition file once. Commit the new state file with the next tick's sync. 2.x manifests have no `omac_schema`; they are
   not rejected and they do not gain 3.0 fields.
3. Re-propose any amendment that was reviewed under 2.x and not accepted.
4. New configuration, all optional with defaults: `retry.environment` (1), `defects.auto_fix` (`design-gap`), `defects.per_reporter`
   (2), `defects.max_auto_fixes` (`auto`), `dispatch.hold_live` (false), `review.invalid_evidence_restarts` (2), `plan.profile`
   (`auto`), `plan.light_max_nodes` (12), `plan.light_max_business_actions` (20). `omac init` writes `retry.environment` with the
   other retry defaults.
5. New commands: `omac defect list|report|grant`, `omac dag decisions`, `omac dag followups`, `omac dag run --wait-decisions`,
   `omac node retry --note`.
6. Worker agents: report `upstream-defect` for finished-upstream gaps and `environment-failure` for push/network/dependency
   timeouts (evidence needs a command that really exited non-zero); the packaged guides say how. Reviewer agents of `live` nodes
   review evidence instead of rerunning live commands. Update custom role prompts that told them otherwise.
7. If you do not want automatic DAG changes yet, set `defects.auto_fix: off`; if you want the 2.4 planning gates, set
   `plan.profile: light`.

### Known limitations

- `_dag.yaml` is one file: two machines that both tick can conflict on rebase. The system-amendment file is local and not synced; on
  another machine an intent without its file is withdrawn and routed again.
- `bounces.environment` is an absolute count outside the amendment baseline; `node retry --worker` does not reset it.
- The acceptance outer loop's inner ticks do not route defects; a report from an increment node stops with exit 20 until the next `dag run`.
- Authoring recovery by system amendment (S3/S15) was exercised on a private Docker Multica, not on a production platform.
- Whether a command "needs the live environment" is left to the Reviewer; the evidence gate does not require live fields.
- Evidence locators and `provides[].ref` are checked for format only, not for existence in the pull request.
- `--wait-decisions` compares the manifest only; a change made only on the platform is noticed at the next `dag run`.
  `dag decisions` `next_actions` list retry/abandon/release, not `accept-nits`.
- Amendment stages use the default of 2 for `review.invalid_evidence_restarts`; develop nodes are bounded by `retry.review`.
- `plan.profile: light` forced by a person also skips the standard checks. A plan that becomes `standard` only by node count keeps
  acceptance `requirements` optional.
- Python 3.14 is not covered by CI; one lightweight-reconcile test asserts a call order that differs there.

## [2.4.1] — 2026-11-06

- Fix: `omac node accept` on a node whose WorkItem still carried a worker blocker (`decision_required`) or a review→worker
  handoff left the handoff and the node's `recovery_marker` behind. The next `dag run` then failed its reconcile with
  "Confirmed merge node ... still has recovery control facts" and every other node stopped. An operator decision now
  retires what it answers: `node accept` clears `decision_required` and `worker_handoff` on the WorkItem (through the existing
  `UpdateMetadata` effect, before `done`) and the node's `recovery_marker`; `node abandon` does the same when the WorkItem
  still carries either fact (platform first; no platform write otherwise, and no engine means a manifest-only abandon as
  before). `node retry` already resets both. The reconcile error now names the repair command (`omac node accept`, which
  can be re-run on a done node whose PR is merged).

## [2.4.0] — 2026-11-06

- New: `omac node hold <manifest> <node> [--reason <text>]` and `omac node release <manifest> <node>` park and resume a node
  that has not been dispatched (for example an end-to-end node that needs a real external account to be prepared first).
  A held node is not dispatched even when its blockers are `done`; its downstream keeps an unsatisfied dependency (it is
  neither released nor cascaded to `blocked`; `node abandon` cannot do this, because it releases a closing node early),
  and independent branches keep advancing. The hold is a runtime fact (`held`, `hold_reason`) in the node's state
  file, so the Loop's manifest sync keeps it. The decision table is `pipeline/decide_hold.py`; `graph.ready_nodes` is the
  one place that skips a held node. Only a `todo` node can be held: a node in progress is refused with an explanation
  (let it finish, or `abandon` it) and a `blocked`/`failed` node must be `retry`-ed first. Both commands write through
  `apply` (`SetNode` + `SaveState`) under the manifest write lock, so a running `dag run` makes them exit 5; stop it first.
- New: when no node is running or waiting for a decision and only held nodes (and the nodes behind them) are left,
  `dag run` / `dag tick` exit 20 (the existing "caller action required" code, no new exit code) with state `held` and a
  report `{held_nodes: [{key, reason}], blocked_downstream, next_actions}`; `next_actions` are the
  `omac node release` commands. The acceptance inner loop stops the same way.
- `dag status` / `dag snapshot` node rows and `node show` carry `held` and `hold_reason`; the table marks `(held)`.
  Existing JSON fields are unchanged; `NODE_KEYS` gains the two new keys.

## [2.3.2] — 2026-11-06

- Fix: one work item that a Worker or Reviewer writes while the Loop is reading it no longer fails the whole `dag tick` /
  `dag run` (`Worker handoff control facts changed while loading ledger ...`, exit 2, every branch stopped until a manual
  restart). Optimistic-read conflicts are now one exception type, `ObservationConflict` (a `PlatformError` subclass), raised
  at the eight places that compare two observations of the same work item (ledger load, handoff dispatch, retry control,
  retry generation, reviewer dispatch baseline, delivery candidate commit, failed-Run facts, handoff intent). The Loop
  catches it per node (collect, reviewer dispatch, formal-Run restore, worker dispatch), logs `observation_conflict`
  with the node and item id, skips that node for the tick, and observes it again on the next. The tick's state and exit
  code come from the remaining nodes (`running` / exit 10 while work continues). A node whose conflict does not clear logs
  one `warning` at the 5th consecutive tick of a `dag run`; there is no stop state for it. Real platform errors (network,
  permission, corrupt data) and drift checks that compare a persisted record with the platform still fail or block as before.
  No new configuration.

## [2.3.1] — 2026-11-05

- Fix: a node no longer waits in `merging` indefinitely when GitHub reports `mergeStateStatus: UNKNOWN` for an OPEN PR.
  GitHub computes mergeability lazily and may leave it `UNKNOWN` for a long time after the base branch moves, so a reviewed,
  CI-green, conflict-free PR was only merged on the rare tick that happened to observe `CLEAN`. `GitHubCodeHost.observe_pull_request`
  now separates the two cases: an explicit `UNKNOWN` on an OPEN PR (no auto-merge, not queued) is `OPEN`, so the single
  merge request is sent and the merge command decides (a refusal takes the existing bounded `retry.merge` bounce or block).
  A failed read (`gh` exit non-zero, timeout, malformed JSON, missing `mergeStateStatus`) is still `UNKNOWN`: keep `merging`, no
  request, no retry spent. The `intent`/`requested` once-only request markers are unchanged. No new configuration.

## [2.3.0] — 2026-11-05

- Behavior change: a DAG develop node whose Reviewer returns a clean `pass-with-nits` (no blockers, valid evidence) is now a
  pass: it goes on to `merging` and `done`, its nits stay verbatim in the review report and the node evidence
  (`omac node show`), and no decision is requested. Before, the node was blocked, every downstream node was cascaded to
  `blocked`, and `dag run` exited 20 until `omac node accept-nits`. The decision is made once, in
  `decide_review.decide_verdict_path`.
- New config: `review.node_nits: accept|decide` (default `accept`; `omac config set review.node_nits decide`). `decide`
  restores the earlier stop. An invalid value is exit 5 before the first tick.
- Unchanged: plan, acceptance and decompose stages and amendments still stop on `pass-with-nits` (`plan accept-nits` /
  `plan continue-review`, `dag amend accept-nits`); reject, exhausted budgets, review-convergence and blockers still stop.
- Documented: a stopped node blocks only its transitive downstream; independent branches keep advancing and exit 20 is
  returned only when nothing is running (`loop._mark_downstream_blocked`, `decide_loop.decide_tick_state`). Regression-tested.

## [2.2.0] — 2026-11-05

- Behavior change: the plan pipeline's human gate (`workflow.human_in_loop`) is a stop, not a wait. After a Reviewer pass,
  a plan or acceptance stage ends `plan create` / `plan resume` with exit 20 (`confirmation-required`) instead of polling for
  hours until the issue is moved to Done. The report carries `next_action` (`omac plan confirm --dag-key <stage-key>`),
  `alternatives` (the same command with `--reject --reason "<what to change>"`) and `then` (`omac plan resume --plan-id <id>`);
  the stop is in `STOP_RELEASES`, so the contract test parses and exercises these commands. `plan confirm` no longer makes a
  waiting process continue: run `plan resume` after it. No other wait on a person remains (the waits left are for Agent Runs).
- New: `omac plan confirm --reject --reason <text>` returns a stage at the gate to its author. The reason is the author's
  rework input (`context.machine_feedback`); the Reviewer's budget is not charged.
- Fix: a replaced `--doc` source (`plan resume --doc`) now reaches the redone stage. The author's issue body carries a
  source-change section (both revisions and a bounded diff of the source set, with the previous delivery's sha256), and the
  Reviewer gets a `source-change:plan` obligation, marked `delivery_unchanged: true` when the delivery is byte-identical to the
  previous one. Before, a redone stage saw only the new source with no sign it had changed, and the Reviewer could not tell an
  unchanged document from a covered change.
- Fix: a `--no-review` pipeline with the human gate on can be resumed after the confirmation.

## [2.1.0] — 2026-11-05

- Behavior change: a plan, acceptance, or decompose review that returns `pass-with-nits` now stops with exit 20
  (`review-nits-acceptance-required`) instead of automatically sending the delivery back to its author. The verdict, the
  report and the reviewed delivery are kept. Release it with `omac plan accept-nits --dag-key <stage-key>` (accept the nits;
  `plan resume` then finishes the stage on the reviewed version) or `omac plan continue-review --dag-key <stage-key>` (send
  the nits back to the author as a counted round). This is how `node accept-nits` and `dag amend accept-nits` already work.
  An unattended caller that used to rely on the automatic rework must now run one of these commands.
- New: `omac plan accept-nits`, sharing the marker and its verified write with `omac node accept-nits`.
- Fix: `omac plan resume --plan-id <id>` works for every pipeline shape. The shape (`--doc` or `--goal`, review, acceptance,
  human gate) is recorded on the source issue as `plan_profile`, so a resume no longer needs `--doc`, `--no-review`,
  `--no-acceptance` or `--no-confirm`; the design text comes from the source issue. A flag that contradicts the record is
  refused with the command to run. Pipelines created before 2.1 keep taking the shape from the command line.
- Fix: every stop of the plan pipeline names one release command that runs as printed (`reason_code` and `next_action`), and
  that command is accepted for that stop. Stops that had no `next_action`, or an unrunnable one (`dag amend propose` before a
  manifest exists, `node retry <manifest> <node>` for a plan issue), now have one. The exit-20 report of plan commands is
  printed on stdout. The report of a stop is now the persisted decision itself: for a machine-gate stop it carries
  `gate=guard`, `phase=review` and a `reason_code` (it used to say `phase=guard` and had no `reason_code`).
- Fix: a machine-gate retry returned the issue to review instead of authoring and left it blocked, so a later `plan resume`
  could not recover; an author Run that ended without submitting had no release at all. `plan resume --restart-active`
  releases both and now acts on the first unfinished stage (plan, acceptance or decompose), not always acceptance; it no
  longer redoes an acceptance document the human gate already approved.
- Fix: `plan resume` never rewrites a manifest that already belongs to the plan or its node runtime state (a node that had
  been dispatched or finished was reset to `todo`); a manifest of another plan whose nodes have started is refused.
- Fix: `plan resume --no-review` no longer releases an issue that stands on a review decision.
- Fix: a review-convergence stop in a plan stage names `plan continue-review` (not `dag amend propose`), and that command
  releases it: the authorized round is not vetoed by the same convergence check.
- Fix: `omac dag` reports keep `--stage review` in the retry command a decision recorded; a Worker blocker names the node id.
- Change: a `--doc` source is a set: a directory is its inventory at one revision; a single file adds its path, the revision
  and the inventory of its directory. Links are not parsed. The source must be committed and its revision pushed. A resume
  republishes the source only when `--doc` is given and its content (ignoring the revision line) differs, and then redoes the
  first unfinished stage. Pass a directory when the design has appendices.
- Release path for issues stopped by 2.0.x on nits (`review-nits-budget-exhausted`): `omac plan continue-review`, then
  `omac plan resume --plan-id <id>`; if the new verdict is still nits, `omac plan accept-nits`.

## [2.0.4] — 2026-11-05

- Fix: `omac plan continue-review` now accepts the stop it is named in. When a resumed `plan resume` reached the review
  budget with a final delivery awaiting re-review after `pass-with-nits`, the issue was blocked with `gate=review`, and the
  command refused it because it only recognized `gate=review-nits`. That stop is now recorded as `review-nits`, and issues
  already blocked with `gate=review` are accepted without editing metadata. The gate and the command's check share
  `core/review_continuation.py`.
- Fix: a machine-gate budget stop no longer names `omac plan continue-review` as its `next_action`. That command cannot
  release this stop.

## [2.0.3] — 2026-11-05

- Fix: a running `omac plan resume` no longer idles forever after a review returns the issue to its author. After a
  `pass-with-nits` or `reject` verdict the process kept treating the return as a resumed authoring attempt and waited for
  a submit that nobody was going to make, because the Reviewer's ending Run looked like that attempt. It now redispatches
  the author, exactly as a fresh `plan resume` would.
- Fix: a resume that waits for an active Run stops waiting when that Run ends. If it ended without a submit, an
  in-progress issue blocks with `completed-without-submit` and no second Run starts.
- Fix: `omac plan create` / `plan resume` hand an issue back to its author only after every direct Run on it has ended,
  and log one `rework_handoff_wait` event while waiting. A machine-gate retry no longer stops with
  `dispatch-run-not-observed` because the author's own Run was still ending.
- Fix: `omac dag run` / `dag tick` hand a rejected delivery back to the Worker only after the Reviewer's Run has ended,
  retrying on a later tick without spending a budget. A Worker that redelivered before that Run ended left the node in
  `in_review` indefinitely, because the next review started no new Run.

## [2.0.2] — 2026-11-04

- Fix: a Reviewer handoff no longer fails while the author's Run is still ending. A submit lands before its Run ends, so
  `omac plan create` / `plan resume` stopped with "still waiting for the prior direct Run to finish". The plan pipeline
  now waits at its usual poll interval until that Run has ended, and `dag run` / `dag tick` retry on a later tick without
  blocking the node or spending a retry budget. Nothing is assigned until then.
- Fix: `omac plan resume --doc` without `--name` takes the name from the `doc-<plan_id>` source issue, and only asks for
  `--name` when that issue does not exist.

## [2.0.1] — 2026-11-04

- Fix: `omac plan create --doc` no longer fails on a design source over 64 KiB. OMAC now publishes every `--doc` file or
  directory source on its own finished source issue, so the acceptance and decomposition stages read it through
  `omac work read` exactly as they read a planner's design.
- Fix: a Run created in the same second as the cutoff is no longer treated as history.
- Tests: the smoke `_run` timeout follows the DAG's `--max-minutes`.
- Smoke: the workflow takes a model input, and a failed run prints every node's evidence (Reviewer verdict and comment).
- Smoke: defaults to the Pi runtime with `gemini-3.8-flash`.

## [2.0.0] — 2026-10-30

2.0.0 removes everything that existed to recover one particular project, splits the engine into three ports, stores
run state as small per-node files, and replaces the in-process mock engine with a private Docker Multica for tests.
It is a breaking release; read **Upgrading from 1.x** first.

### Upgrading from 1.x

- Run state is not migrated. A 1.x single-file manifest is neither read nor modified: `omac` exits 5 and tells you to
  finish that run with 1.x or abandon it, then start a new one with `omac plan create`.
- `engine: mock` (and `OMAC_ENGINE=mock`) now exits 5 with the fix. Tests use a private Docker Multica (`tests/it`).
- `omac init` defaults the engine to `multica`.
- Scripts that call a removed command (below) fail with the usual "invalid argument" error and the command's help.

### Removed

- Project-specific recovery code: 17 modules (about 10,000 lines) for incident-specific evidence handoff, publication and
  SDK checkpoint re-review, owner/source-bound amendment assessment, literal correction, contract repair and state-sync
  recovery, together with their 35 test files and 434 MB of captured fixtures.
- The operator-recovery subcommands that drove them: `dag recover-sync`; `dag amend prepare-owner`,
  `prepare-owner-source`, `resolve-owner`, `resolve-owner-source`, `prepare-review-source`, `observe-review-source`,
  `resolve-review-source`, `prepare-literal-correction` and its observe/resolve steps; `node review-evidence`,
  `continue-evidence`, `review-infrastructure`, `review-publication`, `review-sdk-publication`, `review-sdk-checkpoint`.
  Generic safeguards stay: unknown results are never replayed, drift fails closed, merges are idempotent, amendments
  stay inside their scope.
- The `gzip+base64` full-state transport and its 256 MiB limit (`core/state_transport.py`).
- The built-in `mock` engine (`engines/mock.py`) and the `MOCK_*` / `OMAC_MOCK_*` environment passthrough.

### Changed

- **Three ports.** The engine is `Engine(store, runtime, code_host)`: `WorkItemStore`, `AgentRuntime` and the new
  `CodeHost` (pull-request facts, checks, merge; GitHub adapter). Pull-request methods left the store. The pipeline
  receives `code_host` explicitly. `engines.engine_from_config` is the only CLI assembly path.
- **Layering is enforced.** `lint-imports` (CI) and `tests/test_architecture.py` guard `web → cli → pipeline → engines →
  core → i18n → errors`, the platform-CLI boundary, purity of `decide_*` modules and the absence of captured
  identifiers. Domain types moved from `engines.models` to `core.workitem`; `omac.i18n` no longer imports `core.config`.
- **Pure decisions.** Merge, Worker and Reviewer stage decisions are pure functions (`pipeline/decide_*.py`) with table
  tests; `run_task` and `collect_results` are split into staged functions.
- **State as small files.** Run state lives in `.omac/<name>.state/<node>.yaml` (sha256 header) beside the DAG definition
  `.omac/<name>.yaml`. A save rewrites only changed files with atomic replace; a tick on a 500-node DAG writes
  kilobytes. Machines advancing different nodes merge in git without conflict.
- A crash between creating a work item and recording it now fails closed with an error that names the orphan issue and
  the command that clears it, instead of a raw platform message.
- `exit 0` of `dag run` means the DAG converged and, when required, final acceptance passed (documented; unchanged).
- Tests: about 2,700 tests in roughly two minutes (was about 3,800 tests, 2.6 hours serial). Per-test time, memory and
  temp-file budgets are enforced; `tests/it` runs a private Docker Multica with a scripted agent, a `gh` shim and a fault
  proxy, including a crash/lost-response matrix over every write of a one-node DAG.
- CI installs the pinned Multica CLI and runs the full suite on every push and pull request. A weekly real-agent smoke
  run (`smoke.yml`, schedule and manual only, 20 USD monthly cap) gates releases.
- The current design is in `docs/design/README.md`; contributor rules are in `AGENTS.md`.

### Also added in 2.0.0

- New reviewed amendments use an identity envelope that binds the base manifest
  and acceptance digests plus the review issue, preventing envelope tampering from
  bypassing CAS or confirmation ownership. Legacy amendment files without the
  marker remain readable through the compatibility path. An explicit blocked-node
  decision admits only convergence or contract-boundary amendments; no-submit,
  network, metadata, and Runner failures stay on their dedicated recovery paths.
- A new `retry.no_submit_runs` config key bounds how many consecutive reviewer
  Runs may finish without submitting a verdict before the node blocks with
  `reviewer-run-no-submit-retry-exhausted` (default 2, the previous hardcoded
  budget; must be an integer ≥ 1). Staged multi-Run reviewer behavior can now
  raise it (for example 6) instead of hitting the wall after two no-submit
  Runs. The infrastructure transient-failure budget is separate and stays
  fixed at 2. Invalid values fail closed at `config set`, `dag run`/`dag
  tick`, and acceptance-loop entry.

### Also changed in 2.0.0

- Static project-scoped full-audit control-read failures now remain incomplete
  observations instead of aborting active DAG progress or claiming convergence;
  the audit scheduler retries the static set while active nodes continue.
- Manifest push failures now retain the local commit and back off retries per
  repository/path (10 seconds initially, capped at 5 minutes), suppressing
  repeated `manifest_sync_failed` network warnings without claiming remote sync.
- A blocked DAG review with the dedicated delayed-Run causality decision now
  resumes automatically when one later-visible formal Reviewer Run proves the
  original baseline. OMAC hydrates and validates the current review evidence
  before clearing only that provisional decision; all operator/product
  decisions and ambiguous, stale, malformed, or non-formal Runs remain blocked.
- A new DAG amendment attempt may now supersede either a Reviewer-pass human
  confirmation or a failed-closed decision-required amendment with no active
  Agent Run. The latter accepts both the original blocked state and Multica's
  terminal done projection; ordinary done items and all active or non-terminal
  amendment states still fail closed.
- Directory-backed plan and amendment sources now inventory every file tracked
  by the current Git revision instead of local untracked or ignored files, so
  remote Agents can reproduce the exact authoritative docs digest.
- Review rework now has one convergence authority for both `review_state` and
  Runner control. The same blocker surviving two rework cycles stops at cycle
  three; `scope-expanding` additionally requires two consecutive non-reducing
  blocker transitions plus explicit owners on all open blockers with at least two
  distinct owners. Existing pending or completed-without-submit Worker handoffs
  are blocked before another assign/wake; infrastructure retries remain outside
  the semantic ledger. New plan decomposition runs a strict owner/scope/typed-I/O
  and acceptance-conflict preflight before Reviewer dispatch.
- `work submit` now observes control facts once and hydrates only the attachment
  bodies required by its exact kind and phase, so stale historical evidence
  downloads cannot block a fresh authoring submission.
- DAG `pass-with-nits` rework now consumes the same bounded review budget as
  reject and review-evidence failures. Exhausted nits stop for an explicit
  decision without clearing Reviewer evidence, while an authorized review
  continuation still grants exactly the persisted additional round.
- DAG recovery now separates explicit transient provider/transport failures
  from business non-delivery. The same Worker or Reviewer may receive one
  bounded, restart-safe rerun without consuming business bounce budget;
  authentication, quota, missing-model, security-policy, business-validation,
  and unknown failed Runs stop for a deterministic decision without entering
  completed-without-submit business rework.
- `work show` now marks explicit operator retries as requiring a fresh
  verification submission against the current PR HEAD, even when no code
  change is needed; prior verification remains baseline-only evidence.
- Worker handoff recovery now binds candidate delivery to the platform-observed
  target Run, attachment uploader/task facts, downloaded-byte digest, and remote
  PR HEAD. A recovered delivery rejoins the normal evidence/CI/review path;
  unknown assignment outcomes remain pending or fail closed without duplicate Runs.
- Develop handoff and restart recovery now follow the current delivery review
  subject, while Multica rerun errors observe for an already-created Run before retry.
  Review resets clear the current report projection while preserving ledger/history,
  explicit node retry and amendment stage recovery retire superseded Worker handoff
  intents, and Multica rerun recovery now requires a causal Run match.
- Removed the speculative amendment restart generation/journal framework because
  no current engine offers atomic conditional restart/dispatch. The retained
  `--restart-authoring` flag fails closed before remote access and directs users
  to `--new-attempt --supersedes-issue-id`. New attempts finalize deterministic
  partial shells before dispatch and bind identity to recursive docs content,
  report, manifest, blockers, and the superseded confirmation. Multica's ordinary
  wake path still observes all issue Runs before rerunning a terminal direct Run.
- Typed `consumes` now preserves three distinct policies across manifest,
  amendment, Store attachment, and `work show` round trips: omitted permits
  transitional inputs only from transitive legacy upstream dependencies,
  explicit `[]` permits no external inputs, and a non-empty list remains a
  strict artifact allowlist. Explicit `null` fails validation instead of
  silently widening policy. New DAG plans still declare exact inputs.
- Node contracts can optionally declare typed `evidence_mode`, `produces`, and
  `consumes` boundaries. Manifest lint verifies canonical producers and
  transitive upstream ownership, `work show` projects a compact responsibility
  summary, and Reviewer demands for undeclared downstream artifacts or live
  evidence from fixture nodes now stop as a bounded
  `contract-boundary-conflict` decision instead of consuming Worker rework
  rounds. Manifests that omit the new fields keep their previous behavior.
- OMAC Web now groups collapsed DAG nodes by their exact visible dependency
  signature, draws every represented parent edge, and never double-counts one
  hidden node across summaries. Node detail reads now expose immediate loading,
  error, retry, keyboard, and accessibility feedback while cancelling stale
  Multica requests during rapid selection changes.
- Historical DAG amendment review obligations now bind the same expanded
  WorkItem evidence digest used to build and apply the reviewed amendment, so
  review, ledger, and evidence-reference drift fails closed instead of causing
  a deterministic Reviewer/apply digest disagreement.
- Exhausted plan-stage reviews now have an explicit
  `omac plan continue-review` operator decision. It grants one monotonic,
  persisted review round on the existing work item, restores rejected work to
  the producer through OMAC state transitions, preserves final-nits deliveries
  for Reviewer recheck, refuses active Agent runs without cancelling them, and
  avoids changing project `retry.review` or the reviewed Git revision.
- New review cycles use finite `review_obligations`, a persistent cross-cycle
  blocker ledger, and structured prior-blocker regression results. `review_state`
  is a projection of the same persisted convergence decision consumed by the
  Runner. Legacy in-flight review reports remain readable until OMAC prepares
  their next review subject.
- Deterministic manifest preflight now limits itself to mechanically certain
  facts, including invalid shell syntax and bare Go local package targets. In
  the absence of an explicit typed IO contract, generic command flags, current
  file existence, and scope ownership are no longer treated as artifact-flow
  evidence.
- Machine-gate findings are stored as complete structured attachment payloads.
  Multica metadata contains only a bounded reference and summary pointing
  Authors to `work show.context.machine_feedback`; missing or invalid feedback
  attachments fail closed, while legacy in-flight issues without the new field
  remain readable.
- The evidence schema now requires every contract acceptance item to be mapped
  through `commands[].business_tests` on a command with a non-empty `cmd` and
  integer exit code `0`. Reviewer reports must also include
  `full_review_completed: true` after the entire review scope is complete.
- This schema upgrade has no legacy mode. Existing Worker verification files
  must add concrete `{acceptance, test}` entries under successful ordinary or
  integration-gate commands. Existing Reviewer reports must add
  `full_review_completed: true` before they can be submitted again.
- Tests: the per-test CPU time, memory and temp-file budgets and the fixture size limits are removed; unit tests stay
  fast through the architecture, and markers only select what runs.
- Releases no longer wait for a successful smoke run on the tagged commit.
- The smoke run is manual only and runs on the self-hosted Linux runner with the Codex or Pi already signed in there;
  the metered API key and the monthly spend ledger are removed.

## [1.0.0] — 2026-07-17

The first public release turns Multica's workspaces, work items, and Coding
Agent runtimes into a controlled software delivery process. A requirement can
move through design, dynamic planning, implementation, verification, review,
merge, and final acceptance without relying on one Agent to supervise the
whole delivery.

### Added

- A reviewed planning chain for design, acceptance criteria, project rules,
  and an Agent-authored manifest DAG.
- Dependency-aware parallel execution through Multica workspaces and runtimes.
- A deterministic Loop for result collection, ready-node dispatch, evidence
  gates, bounded rework, recovery, merge conditions, and completion decisions.
- Structured verification evidence and independent Reviewer verdicts for each
  delivery node.
- Optional CI and Pull Request integration, followed by flow-based acceptance
  on the integrated default branch.
- Persistent execution state, stable exit codes, and recovery guidance for
  interrupted deliveries.
- Human and Controller Agent entry points that use the same CLI protocol and
  see the same delivery facts.
- A local read-only web interface for inspecting plans and execution state.
- Built-in Agent Team templates for planning, orchestration, implementation,
  review, and acceptance.
- English and Simplified Chinese documentation, plus project-local language
  selection for packaged Guides.

### Public demonstration

The [Webhook Inbox demo] shows the complete path from one requirement to five
reviewed Pull Requests and an accepted FastAPI service. Its checked-in evidence
records 86 passing tests, 97.18% coverage, CI across Python 3.10–3.13, and
11/11 final acceptance flows.

[Webhook Inbox demo]: https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox
[2.0.1]: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v2.0.1
[2.0.0]: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v2.0.0
[1.0.0]: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v1.0.0

# oh-my-multica design (3.0)

This is the current design of `omac`. Section numbers are stable: code comments cite them as `docs/design/README.md §7.3`.
Rules for working in the repository are in [`AGENTS.md`](../../AGENTS.md); this document explains the *why* and the
shape of the system. The 2026-05 design draft (kept outside the repository) is history; where it differs, this file wins.

## 1. Scope and the one rule

`omac` is a **general-purpose delivery process**: plan a goal into a DAG of nodes, dispatch each node to an Agent through
a work-item platform, collect the results, gate them with machine checks and an independent review, merge, and repeat
until the DAG converges and the acceptance document passes.

**Only generic mechanisms belong in `src/omac`.** A mechanism is generic if it protects every project that uses the
process (for example "an unknown result is never replayed"). Anything that exists to recover one particular incident
(captured identifiers, special-cased node names, bespoke repair commands) is operator tooling: it is written outside the
package, reviewed, run once and deleted. `tests/test_architecture.py` rejects captured literals and project names in
source and tests; the pull-request template asks the same question.

## 2. Principles

1. **The Loop is deterministic.** Agents write deliveries; they never decide control flow. What happens next is a pure
   function of observed facts (`decide_*`), applied by a thin shell that performs one effect at a time.
2. **Facts come from the platform and the manifest, never from memory.** Every tick starts by observing.
3. **Errors teach (2.3).** Every user-facing error says what is missing, how to fix it, and gives a copyable command;
   invalid arguments print the command's full help. All text goes through `omac.i18n.ui(english, chinese)`.
4. **Retries are explicit decisions (2.4).** A business failure is not retried automatically. Bounded, counted returns to
   the Worker (`retry.worker|ci|review|merge`) exist for machine-detectable rework; when the budget is spent the node
   blocks and `omac` exits 20 with a structured report. `omac node retry|abandon|accept` are the decisions. Two bounded
   automatic paths exist since 3.0, both counted and both stopping in exit 20 when spent: one same-Worker retry of an
   `environment-failure` report, and the upstream-defect route (7.5), which inserts a fix node instead of retrying.
5. **Unknown results are never replayed; drift fails closed.** If a write may or may not have happened, `omac` persists an
   intent *before* the effect, and after a crash it observes instead of repeating. If the platform no longer matches what
   was recorded, it stops and says so.
6. **One writer per manifest**, guarded by a lock; the manifest is small by construction (4).

## 3. Structure and layering

```
web → cli → pipeline → engines → core → guide → i18n → errors
```

| Layer | Role |
|---|---|
| `web` | Read-only dashboard. Parses parameters, calls command functions, returns their JSON unchanged (13). |
| `cli` | Argument parsing, exit codes (`cli/exit_codes.py`, `sys.exit` only in `cli/main.py`), engine assembly. |
| `pipeline` | The Loop: reconcile, result collection, gates, dispatch, delivery, acceptance, plan, amendment. |
| `engines` | Ports (`store.py`, `runtime.py`, `codehost.py`) and adapters (`multica.py`, `github.py`). |
| `core` | Domain model, manifest I/O, validation, evidence, graph, git state sync. No platform knowledge and no port writes. |
| `guide` | Packaged, bilingual, machine-consumed guides (9). |

Enforced by `lint-imports` (contracts in `pyproject.toml`, function-level imports count) and by
`tests/test_architecture.py`: platform CLIs (`multica`, `gh`, `curl`) run only inside adapters; `git` and `bash -n` are
the only other subprocess users, on an explicit allowlist; `decide_*` modules import neither `engines` nor
`subprocess`; `create_engine` is called only inside `engines`. The single permanent layering exception is the `omac web`
command starting the web server.

## 4. State storage

A DAG is two things on disk, both plain text, both committed to git:

```
.omac/<name>.yaml            DAG definition: meta + node definitions. Changes only on plan and amendment.
.omac/<name>.state/<node>.yaml   One file per node: work_item_id, status, merge facts. A few hundred bytes.
.omac/<name>.state/_dag.yaml     DAG-level runtime facts (the defect ledger, a pending human amendment, the live nodes the Loop already held). Written only by the Loop; absent until there is one.
```

The definition digest (`core/amendment.py::manifest_definition_digest`) leaves out every runtime `meta` key
(`RUNTIME_META_KEYS`), and `_dag.yaml` is not part of the definition, so a tick never changes a reviewed amendment's
base. `meta.omac_schema` is the schema a release writes (3 in 3.0); an older omac refuses a higher value instead of
silently dropping fields. A long-running `dag run` compares the definition file with what it last wrote at the start of
every tick and reloads it when someone else edited it (`node retry --worker`), so a save never puts a stale definition
back. Git carries only state with business meaning: node, Run, defect and decision facts. Volatile operational data (the
reconcile-audit schedule: last full scan, ticks since) is host-local (`core/manifest.py::local_runtime_file`, beside the
write lock in `$TMPDIR`), so a tick that changes nothing commits and pushes nothing; a lost file only forces one early full
scan. A manifest that still holds `reconcile_audit` (2.x `meta`, 3.0 `_dag.yaml`) drops it on load; the next save removes
it from git once.

* **Incremental atomic writes.** `save_manifest` compares each file's content with what the last load/save left on disk
  and rewrites only what changed: write a temporary file in the same directory, `fsync`, `os.replace`. Node state files
  are written first and the definition file last, so a record never claims progress that is not durable. A killed
  process leaves every file either old-and-whole or new-and-whole.
* **Integrity.** Each state file starts with `# sha256: <digest of the body>`. A mismatch (hand edit, truncation, bad
  merge) is refused with the file path and `git checkout -- <file>`.
* **Sync through `origin/main`.** `core/gitsync.py` commits the definition and the state directory after each tick.
  Machines advancing different nodes touch different files, so git merges them without conflict; a conflicting push
  is rebased only when every unpushed commit touches nothing but those files. A failed sync names its step: `fetch`,
  `upstream`, `safety` (user commits or a moving HEAD), `rebase_not_started` (git refused to start, e.g. uncommitted
  changes; nothing is aborted), `rebase_conflict` (aborted, remote untouched), `rebase_abort`, `push` / `push_retry`. The
  local commit is kept and the push backs off (10 s doubling to 300 s); when the same step keeps failing until the backoff
  sits at its cap, the tick raises `NeedsDecision` (exit 20) with the step, git's error and repair commands instead of
  warning forever. The count is per process: one `dag tick` per process warns each time. The system-amendment file
  (`.omac/<name>.system-amendment.yaml`, 7.5) is local and not synced.
* **Bounded size.** A node's state file is limited to 64 KiB at save time (the error says to keep large payloads as
  platform attachments). The definition holds no run history; the amendment ledger in `meta` is replaced per amendment.
  Large payloads (captured sources, transcripts) are attachments on the platform, referenced by digest.
* **No journal to archive.** Effect intents (worker handoff, review cycle, merge request) live on the platform issue; a node file
  keeps its runtime facts (`work_item_id`, `status`, `merged`, `merged_at`, `merge_request_state`, `recovery_marker`, plus `held` / `hold_reason`
  of a held node, the `retry_note` of the last `node retry --note`, and `defect_pending` while a defect report awaits routing), and the only DAG-level
  records are the amendment ledger above and `_dag.yaml`. Nothing accumulates, so nothing is
  archived: `tests/test_manifest_state.py` (500 nodes with every fact set average well under 4 KiB) and
  `tests/test_amendment_budget_retention.py` (120 amendments leave the manifest the size of the first few) pin it.
* **No 1.x compatibility.** A single-file 1.x manifest (inline `work_item_id`, or the old compressed envelope) is not read
  or modified: the error says to finish that run with 1.x or abandon it, then run `omac plan create` with 2.0.
* **Node ids** become file names: letters, digits, `.`, `_`, `-`.

## 5. CLI contract

### 5.1 Exit codes and errors

| Code | Meaning |
|---|---|
| 0 | The DAG converged (nothing running, blocked or failed; `done` and `abandoned` count) and, when required, final acceptance passed. |
| 1 | General error. |
| 2 | Platform or network error, including a missing work item. |
| 3 | Authentication error (platform CLI not signed in). |
| 5 | Validation failure (lint, evidence schema, unknown engine, 1.x state). |
| 10 | Still advancing (`dag tick`; `dag run` at `--max-rounds`/`--max-minutes`). Rerun the same command. |
| 20 | A caller decision is required; a structured report is attached. |

The contract is stable and safe for scripts to branch on. Business-layer `OmacError` subclasses (`errors.py`) map to codes.

### 5.2 JSON output

Commands take `--output json|table`; agents and the web use JSON. `dag run --output json` prints
`{state, done, running, failed, dispatched, report}`; on exit 20 `report` is
`{failed_nodes, blocked_downstream, next_actions}`, and `next_actions` are copyable commands. When only held nodes
(7.5) and the nodes behind them are left, `state` is `held`, the exit code is also 20, and `report` is
`{held_nodes: [{key, reason}], blocked_downstream, next_actions}` with one `omac node release` command per held node.
`dag status` node rows carry `held` and `hold_reason`.

3.0 adds three read-only commands whose JSON is new (no existing field changed):

* `omac dag decisions <manifest>` prints `{decisions: [{decision_key, kind, node, status, work_item_id, reason, next_actions}]}`;
  `kind` is the node status (`blocked`, `failed`) or `held`, and `decision_key` (`<node>:<status>:<work_item_id>`) is stable
  while the decision stands. It reads the manifest only.
* `omac dag followups <manifest>` prints `{closeout_node, followups: [{followup_id, node, work_item_id, nit}]}`, the nits of
  merged `pass-with-nits` nodes (`followup_id` is `<node>#<n>`).
* `omac defect list <manifest>` prints the defect ledger (`used_global`, `granted_global`, `used_by_reporter`, `granted_by_reporter`, `entries` — a `queued` entry carries `waits_on`,
  `inbox`, `applying`, `pending_reporters`, `pending_human_amendment`); `defect report` and `defect grant` write through the
  Loop's own path under the manifest write lock.

Exit 20 from a defect stop (`defect-needs-owner`, `-unroutable`, `-fix-generation-cap`, `-recurred`, `-budget-exhausted`) has
the usual report; its `next_actions` come from `decide_defects.render_release`.

## 6. Configuration

`.omac/config.yaml` (precedence: file < `OMAC_*` environment < flags): `engine: multica`, `workspace`, `project`
(required: every issue belongs to a project), `roles` (planner, orchestrator, workers, reviewers, acceptor),
`defaults` (`max_parallel`, `poll_interval`, `coverage_gate`), `workflow` (human-in-the-loop, review, acceptance doc),
`ci` (`check_command`, `pending_exit_code`, `timeout_minutes` 30), `merge.command` (default `gh pr merge {pr_url} --squash --delete-branch`),
`retry` (`worker|ci|review|merge|environment`, `no_submit_runs`, `capacity_wait_hours` default 12: how long a Run that failed on quota / rate limit is waited out before the node blocks; `environment` is the automatic same-Worker retries of an
`environment-failure` report, default 1), `defects` (`auto_fix: off|design-gap`, `per_reporter` 2, `max_auto_fixes: auto` = max(5, ⌈0.15 × nodes⌉) — raised by `omac defect grant --global`, which adds to `_dag.defects.granted_global` and does not edit the config —, `per_suspected` 2),
`dispatch.hold_live` (default false: hold each ready `evidence_mode: live` node once for a person to
release), `review.node_nits` (`accept` default | `decide`: whether a develop
node's `pass-with-nits` is a pass or an exit-20 stop; plan stages and amendments always stop),
`review.invalid_evidence_restarts` (default 2: re-dispatches of the Reviewer on one subject after invalid review evidence),
`plan` (`profile: auto|light|standard`, `light_max_nodes` 12, `light_max_business_actions` 20), `reconcile`, `acceptance.max_rounds`. `omac init` writes it;
the engine prompt defaults to `multica`.

## 7. The Loop

### 7.1 init

Writes the config, picks or creates the platform project (the engine prompt defaults to `multica`), and offers to
provision agents from the packaged templates.

### 7.2 plan

`plan create|resume` runs a pipeline of authoring tasks (design, decomposition, review) through the same
produce → machine gate → bounded review loop as any node, then writes the manifest and the acceptance document and
commits them together.

The shape of a pipeline (`--doc` or `--goal`, review, acceptance, human gate) is recorded on its source issue as
`plan_profile`, so `plan resume --plan-id` takes no shape argument. A `--doc` source is a set: a directory is its
inventory at one revision; a single file adds its path, the revision and its directory's inventory (links are not parsed);
it must be committed and pushed. `plan resume` never rewrites a manifest that already is the plan's.

Two bounds keep a plan stage from looping. The machine gate stops when a round does not lower the number of errors
(`guard-non-reducing`, the report lists every error; the budget `max_revisions` still applies). A Reviewer whose report
fails the evidence gate is asked again on the same subject at most `review.invalid_evidence_restarts` times, then the stage
stops with `review-evidence-invalid` (released by `plan resume`). Both stops are rows of `decide_task.TRANSITIONS`.

A plan has a profile, `light` or `standard`, decided by code (`review_preflight.plan_scale`): light when the decomposition has
at most `plan.light_max_nodes` nodes and the acceptance document at most `plan.light_max_business_actions` business
actions, otherwise standard; only the operator's `plan.profile` overrides it, the model never chooses. The result is
recorded in `meta.plan_scale`. Light adds no gate to the 2.4 plan. Standard also requires `requires` on every non-empty
`consumes` entry (and a `ref` on every required `provides` item) and a `meta.closeout_node` that depends on every other
node (`plan_manifest_preflight`; amendments are not bound by it). `requirements` (acceptance v3) is required where the
acceptance alone proves the plan standard, that is by its business-action count; the node count is known only after
decomposition, so a plan that becomes standard by node count keeps `requirements` optional (checked when present).

Every stop of a stage is built by `decide_stop.build_stop`, so the persisted decision and the exit-20 report are one dict.
`STOP_RELEASES` says which command releases which stop, `render_release` spells those commands, and `decide_release`
decides whether `plan continue-review` / `plan accept-nits` may act and what they write. `pass-with-nits` stops a stage
for a person, unlike a develop node (a develop node's `pass-with-nits` is a pass under the default `review.node_nits: accept`,
decided once in `decide_review.decide_verdict_path`; `decide` stops it for `node accept-nits`): `accept-nits` finishes the stage on the reviewed delivery, `continue-review`
sends the nits back as a counted round. A resumed issue is classified by `decide_stop.classify_resume`.

The human gate is such a stop too (`confirmation-required`): after a Reviewer pass the stage ends with exit 20 and the
report names `plan confirm --dag-key <stage-key>`, the alternative `--reject --reason "<what to change>"`, and `then`
`plan resume`. Nothing in the stage machine waits for a person; the only waits left are for Agent Runs. `--reject`
returns the stage to its author without counting a review round and hands the reason over as machine feedback.

When `plan resume --doc` replaces a published source, the stage that is redone gets the change as facts: the issue body
carries both revisions and a bounded diff of the source set (`plan_source.source_change`), and the design `source_refs`
entry records the digests of the replaced source and of the previous delivery. The review obligations then include
`source-change:<label>` (`core/review_convergence.source_change_obligations`), marked `delivery_unchanged` when the new
delivery is byte-identical to the previous one. The review subject digest is unchanged on purpose (it would orphan
verdicts of running pipelines); the obligation set is rebuilt for every reopened stage because the rework resets it.

### 7.3 dag run

One tick:

```
reconcile → result collection (collect_results) → evidence / CI / merge gates → ready nodes → dispatch → converge or exit 20
```

* **reconcile** reads the *running nodes* (进行中节点) and platform facts, repairs manifest/platform drift, and runs a
  low-frequency full audit.
* **result collection** walks each running node through staged functions (`worker blocker`, `handoff convergence`,
  `worker handoff`, `review phase`, then `in_progress | in_review | merging` collectors).
* **Pure decisions** live in the `decide_*` modules (7.3.1): facts in, a named action or an ordered plan of effects out,
  table-tested without a platform. The shell reads, decides, then applies the plan.
* **Merge delivery** is a state machine on `merge_request_state` (`None → intent → requested → observed MERGED`). A marker
  is never followed by a second merge request; after an unknown outcome the node stops (exit 20) and `omac node retry`
  is the explicit decision. Success is concluded only from the *observed* pull request, never from the command's exit code.
* **ready nodes** (就绪节点) are nodes whose blockers are `done` or `abandoned`. When fewer slots than ready nodes remain, `graph.prioritize_ready` orders them: owner of a complete flow
  (`acceptance_claims`) first, then most transitive downstream nodes, then manifest order.
* **Waiting for a person (`dag run --wait-decisions`).** When nothing is running and a decision is pending the run does not exit 20:
  it releases the manifest write lock (so `node retry|release|accept` work) and polls the manifest without it. Each decision is
  announced once as a `decision_pending` event (stderr), keyed by `decision_key`; a decision that was resolved and comes back is
  announced again. When the set of decisions changes the run takes the lock again and continues; it exits 0 on convergence and
  10 at `--max-minutes` (the wall clock runs across the wait). Without the flag nothing changes. Known limit: it compares the
  manifest only, so a change made only on the platform is not noticed until `dag run` is started again.
* **A stopped node blocks only what depends on it.** `loop._mark_downstream_blocked` marks the transitive downstream
  (`graph.downstream_of`) of needs-decision nodes `blocked`; independent branches keep being dispatched, and the tick
  reports `needs_decision` (exit 20) only when no node is running.

#### 批量修复 2026-11-13：CI 非阻塞观察与 runner 信号

* **Problem.** The default CI command was `gh pr checks {pr_url} --watch --fail-fast`: `advance_delivery` held the one
  tick (and the manifest write lock) for the whole CI run (~10 min), so no defect routing, dispatch or reconcile ran meanwhile.
* **Mechanism.** The check is observed once per tick. `CodeHost.check_pull_request(..., pending_exit_code)` returns
  `PullRequestCheckResult.pending` when the command ends with `ci.pending_exit_code` (default command: `gh pr checks`
  without `--watch`, exit 8 = pending; `gh`'s "no checks reported" right after a push counts as pending too). `advance_delivery`
  then changes nothing and returns `pending` (`decide_worker.AfterCi.WAIT`, event `ci_pending`); the node stays `in_progress`
  with the delivered item and the next tick observes again. A tick that changed nothing waits `defaults.poll_interval`
  (7.3), so `gh` is called about once per poll interval per waiting node. `ci.timeout_minutes` counts from the first pending
  observation, kept in host-local runtime data (`ci-wait`, never in git; a lost file restarts the wait, so it is bounded and
  safe) and expiry is a timed-out failure through the normal `retry.ci` bounce (`decide_ci.ci_wait_expired`).
* **Compatibility.** An explicit `ci.check_command` without `pending_exit_code` stays a blocking command whose exit code is
  the verdict; set `pending_exit_code` to make a custom command non-blocking. Cost: one `gh` call per poll instead of one long
  call; `ci_check` is no longer visible as a manifest status while waiting (it only existed in memory during the blocking call).
* **Merge conflict is not "no checks".** GitHub runs no CI for a PR that conflicts with its base, so `gh pr checks` reports
  "no checks reported" forever; treating that as pending would only wait for the timeout, and as red would blame CI.
  Whenever a check is not green (pending or failed), `advance_delivery` reads `CodeHost.observe_pull_request`;
  `PullRequestObservation.conflicting` (adapter: `mergeStateStatus == DIRTY` or `mergeable == CONFLICTING`) turns the result into a
  bounce back to the Worker whose comment says to rebase and resubmit (`decide_ci.ci_unpassed_is_conflict`). It reuses the CI
  gate's bounce (`retry.ci`), not the merge-request machinery: no merge intent exists yet at this gate. An `UNKNOWN`
  mergeability stays pending (bounded by `ci.timeout_minutes`).
* **Runner signals.** `dag run|tick` already stop gracefully on SIGINT and SIGTERM and re-arm SIGINT in a background job
  (`cli/commands/dag.py::_Stop`, #185; `nohup cmd &` starts with SIGINT ignored). No further change.

#### 7.3.1 Observation → Plan → apply

Every decision has the same shape. A frozen **`Observation`** holds the facts a decision reads (`decide_effects.py`);
the decision returns a **`Plan`**: an ordered tuple of frozen **`Effect`** values plus the outcome name the caller reports.
`pipeline/apply.py::apply(effects, ports, state)` is the only code that turns an effect into a port call: strictly in
order, one at a time, one result per effect, never retrying and never swallowing an exception. `tests/test_architecture.py`
(`inline_effects`) fails the build on a `store`/`runtime`/`code_host` write anywhere else in `src/omac` (the adapters and
the interactive `omac init` wizard excepted). Direct manifest writes (`set_node`, `save_manifest`) that predate `SetNode`
remain in a few shell modules; `MANIFEST_WRITE_BASELINE` in the same test pins their count per file, which may only shrink.

| Effect | Port call |
|---|---|
| `UpdateStatus`, `UpdateMetadata`, `AddComment`, `Assign`, `ClearAssignment`, `ResetReview`, `PrepareReviewCycle`, `CreateWorkItem`, `SetNodeContract`, `SetAuthoringIdentity`, `NormalizeConfirmedMerge`, `RestoreAuthoringGeneration` | `WorkItemStore` |
| `Wake`, `AcknowledgeAssignment`, `DispatchReviewer`, `CancelRun` | `AgentRuntime` |
| `RequestMerge` | `CodeHost` |
| `SetNode`, `SetDagFact`, `SaveState` | the manifest (in memory, then atomically on disk) |

Crash consistency is a property of the plan, not of the executor: the *order* is the contract. `RequestMerge` (an effect
whose outcome may be unknown and must never be replayed) is rejected by `validate_plan` unless the same plan first has
`SetNode(merge_request_state="intent")` followed by `SaveState`. Decisions that cannot be a single plan because a read must
sit between two effects are split at that read; the read is a stage or an explicit step, never hidden inside an effect:

* **Merge** (`decide_merge.py`): precheck → observe → one of {settle, record pending, block, *intent plan + one
  `RequestMerge`*} → observe again → {settle, bounce, block}. Each arrow that follows a read is a separate call to `apply`.
* **Authoring task** (`decide_task.py`, `tasks.run_task`): an explicit **stage transition table**
  `(stage, fact) → (next stage, effects)` with 41 rows. A handler per stage observes (reads, waits for a Run, calls the
  producer) and names the fact; the table gives the effects to apply before entering the next stage. A budget stop is two
  rows with a read between them (`MACHINE_GATE → BLOCK_BUDGET → STOPPED`). `tests/test_decide_task.py` pins every row and
  checks that the table is closed (every stage reachable, only `STOPPED`/`DONE` are terminal, an unknown pair is an error).
* **Result collection / reconcile** (`decide_loop.py`, `decide_transitions.py`): block, rework and finish transitions are
  plans (`plan_block`, `decide_done_develop`, `rework_to_producer`...). The Worker/Reviewer handoff checkpoints in `loop.py`
  are guarded protocols: one effect per checkpoint, each followed by a control read that can stop the handoff, so each
  `apply` there carries exactly one effect and the guard between two checkpoints is an explicit observation point.
* **Explicit stage recovery** (`omac node retry`, amendment apply; `pipeline/stage_recovery.py`): the checks and digests
  are pure (`core/stage_recovery.py`); the writes are `plan_stage_reset` → read the review subject → `plan_enter_review`,
  and an authoring retry publishes its recovering intent, then restores the generation, each write read back before the
  next. `core.amendment.apply_amendment` receives this writer as `recover`, so `core` itself writes nothing.

### 7.4 Stage handoff

Worker and Reviewer turns are chained by persisted intents so a restart resumes instead of repeating: a Worker handoff
binds the target agent, the Run, a baseline of earlier Runs and the verification attachment; a submit is accepted as a
delivery only if the Run, the attachment and the pull-request head line up (`delivery_identity`, sealed by the
controller). The Reviewer is dispatched against a subject digest; a verdict applies to that subject only. A rejected
delivery returns to the Worker within the review budget and must move the pull-request head.

An author submits before its Run ends, so a Reviewer dispatch first observes the Runs: while another agent's direct Run is
active nothing is assigned, the plan pipeline waits at its poll interval and the Loop retries on a later tick. That wait
is neither an error nor a budget, and it lasts as long as the Run does. The adapter's own check in `wake` remains for a Run
that appears after this observation.

A Reviewer also submits before its Run ends. The plan pipeline returns the issue to its author on the same terms: it
dispatches once no direct Run on the issue is active, whether that Run is the Reviewer's or, on a machine-gate retry, the
author's own. A process treats an issue as a resumed attempt only on its first produce; every later produce follows a
return it made itself. The Loop's Worker handoff makes the same observation for another agent's direct Run and retries on
a later tick; otherwise a Worker that redelivers first gets a review that starts no new Run.

### 7.5 Dependencies and abandonment

A node is ready when every blocker is `done` or `abandoned`. `node abandon` turns a blocked node into `abandoned` and
unlocks its downstream. `node accept-nits|accept|retry` are the other explicit decisions (`accept-nits` is needed only under `review.node_nits: decide`).
Each of them retires what it answers: `accept` and `abandon` clear the issue's `decision_required` and `worker_handoff` (`decide_transitions.retire_decision`) and the node's `recovery_marker`; `retry` resets both through stage recovery. A done, confirmed-merge node never keeps a decision or handoff.

`omac node hold|release` park and resume a node without changing the graph. Holding sets `held` (and an optional
`hold_reason`) on a `todo` node; `graph.ready_nodes` is the single place that skips it, and because it stays `todo` its
downstream never sees a satisfied blocker, so nothing is dispatched behind it and nothing cascades to `blocked`
(unlike `abandon`, which satisfies the downstream). Independent branches advance normally. The decision table is
`pipeline/decide_hold.py`: only a `todo` node can be held. A node in progress is refused rather than stopped, because
its work item, Run and PR live on the platform and a manifest flag would not stop them; a `blocked`/`failed` node is
sent to `node retry` first. Both commands apply `SetNode` + `SaveState` under the manifest write lock (a running
`dag run` makes them exit 5 instead of racing the Loop). `held`/`hold_reason` are runtime facts in the node state file,
so the Loop's manifest sync only commits them. With nothing running, nothing failed and a held `todo` node left, the
tick state is `held` (`decide_loop.decide_tick_state`): `dag run`/`dag tick` exit 20 with the report of 5.2.
With `dispatch.hold_live` the Loop holds a ready `evidence_mode: live` node itself, once (`decide_hold.plan_auto_hold_live`:
`SetNode` + `SetDagFact("auto_held")` + `SaveState`, event `auto_hold`); after `node release` it is an ordinary node.

**Environment failures and retry notes.** A Worker `environment-failure` blocker report (the environment, not the task, stopped
it; evidence must hold a command that really exited non-zero) is retried once by the same Worker without spending
`retry.worker`: `decide_worker.decide_environment_failure` reads `bounces.environment` against `retry.environment`, and the
retry is the ordinary Worker handoff with a new generation. When it is spent the node stops (exit 20) and `next_actions` carry
`omac node retry … --worker <next worker>`. `node retry --note <text>` (at most 2000 characters) stores the note as the node's
`retry_note`; it is rendered into the next authoring issue body and cleared by a retry without a note (it is not review
feedback, because the Loop resets review feedback before it dispatches). Known limit: `bounces.environment` is an absolute
count outside the amendment baseline, so a new Worker chosen by `node retry --worker` starts with the spent budget and its
first environment failure stops at once.

**Follow-ups.** `core/followups.py` lists the nits of merged nodes whose verdict was `pass-with-nits`. The review of
`meta.closeout_node` gets one `followups:triage` obligation (`pipeline/followups.review_obligations_for`, built when a review
subject is prepared, only if there is something to triage): its evidence must name every follow-up id with `fix`, `defer` or
`drop`, otherwise the evidence gate refuses the report.

#### Upstream defects

A Worker whose work is blocked by something a *finished* upstream node should have delivered reports
`omac.worker-blocker/v3` `upstream-defect` (`defects`: class, `suspected_node`, anchor ids, design ref, expected/observed,
repro); a Reviewer whose every open blocker names a finished upstream node as `owner` produces the same report instead of
rework (no review bounce is spent). The report lives on the platform item (`decision_required`, at most 6 KiB) and the node
carries `defect_pending`. Between two ticks of `dag run|tick` (the process that holds the write lock), `pipeline/defects.py`
fingerprints each defect (`core/defects.py::fingerprint` = suspected node + sorted anchor ids; prose never counts), and
`decide_defects.DEFECT_ROUTES` — the one routing table — picks a route: stop for a person (`defect-needs-owner`,
`-unroutable`, `-fix-generation-cap`, `-recurred`, `-budget-exhausted`, each with copyable release commands), wait
(`queued`: a human amendment is pending, or another fix of the same node is in flight), attach the reporter to a fix that
exists, rerun the reporter once against a fix of the same node that was merged *after the reporter's latest Run started*
(`retry-after-fix`; `pipeline/defects.py::_other_fix_after_run` compares the fix node's `merged_at` with the newest Run
`created_at` from `AgentRuntime.list_runs`; a fix that is older was already part of what failed, so the defect falls through
to the next row; when either time is unknown the old behaviour is kept and the log says why), stop once the suspected node
has `defects.per_suspected` merged fixes (`defect-node-fix-cap`), or `auto-fix`. An auto-fix is a *system amendment* through the ordinary amendment ledger
(`core/amendment.py::build_system_amendment`, identity `…/v3-system`): code builds it, no Agent reviews it and no person
confirms it, and its trust is a fixed whitelist — `add` fix nodes (each with `contract.defect_refs`, `blocked_by` only
finished or added nodes), append-only `update` of a reporter's `blocked_by`, `resume` of a reporter's authoring. A fix node
copies the suspected node's worker, reviewer, scope and gates; its review is the ordinary node review. The reporter waits on
it through `blocked_by`, so it cannot be retried before the fix merges. Order and crash safety: intent
(`_dag.defects.applying`, saved) → `<name>.system-amendment.yaml` → `apply_amendment` → reload → `fixing`; fingerprint
states (`queued → applying → fixing → fixed → recurred | needs-owner`) only move forward, budgets are charged when a fix
becomes `fixing`, and a restart finishes or withdraws the intent. Operators use `omac defect list|report|grant|resolve` (`resolve` records a fix merged outside OMAC: the fingerprint becomes `fixed` with its PR url and counts toward `per_suspected`).

#### 批量修复 2026-11-13：缺陷 queued 的起因跟踪与分层预算

两个问题同属“路由结果没有说清自己依赖什么”。

* **queued 的起因是账本事实。** 旧实现靠进程内的 `_QUEUED_SEEN` + `_wait_signature` 判断“等待的东西变了没有”：白名单式、重启即丢、
  看不见起因本身，operator 也无从得知一个 queued 缺陷在等什么。现在 `_record(…, "queued")` 把起因写进该指纹的账本条目
  `waits_on`（有界、排序、可比较，最多 8 项）：`amendment:<id>`（人工 amendment 待确认）与 `fix:<节点>`（同一疑似节点上还在
  `applying|fixing` 的其他修复）。`advance` 在每轮开头先问 `_wait_ended`——只读 manifest，不读平台——任一记录的起因与现状
  不同（含消失）就重新路由；进程内签名仍保留，作用只是 #187 的不变量：起因不变则不路由、不重复日志、不读平台。
  `defect list` 直接显示 `waits_on`。随这次改动，`_promote_fixed` 的结果与该轮其余账本变更一起落盘（旧实现只在无其他变更时才落盘，
  修复合并后的 `fixed` 可能每轮重算）。
* **预算是两层，停止报告要点名。** 层一：`defects.per_reporter` + `defect grant --reporter`；层二（DAG 全局）：
  `defects.max_auto_fixes` + 新的账本字段 `granted_global`（`defect grant --global --count N`）。`DefectFacts` 分成
  `reporter_budget_left`/`global_budget_left`，路由表仍是同一行 `defect-budget-exhausted`（reason_code 不变，脚本照旧），
  但 `decision_required` 增加 `budget: {layers, reporter:{used,limit}, global:{used,limit}}`，`next_action` / `alternatives` 按用尽的层
  各给一条可复制的 `omac defect grant`（`decide_defects.render_release(…, layers)`）。预算停止的缺陷以 `queued` + `stop` 记入账本，
  `grant --global` 凭此找到被全局上限停住的报告者并重新排队；`grant --reporter` 只释放该报告者。授予错误的层不会放行，
  下一轮仍按剩下的层点名。
* **不改：** 不把全局上限做成自动放大（上限是人对 token 开销的显式承诺）；不改 `defect-budget-exhausted` 的 reason_code；
  未记录 `waits_on` 的旧版 queued 条目不被 `_wait_ended` 唤醒，由进程内签名覆盖，下一次完整路由时补记。

### 7.6 Acceptance and amendments

When the DAG converges and the manifest requires acceptance, an outer loop runs the acceptor against the acceptance
document (`acceptance.max_rounds`, independent of `retry`). Failed flows produce an incremental decomposition that is
merged through a system amendment (7.5), recorded per flow in the defect ledger; a flow that fails again after its increment
was merged stops the loop. `dag amend propose|accept` changes a running DAG: a reviewed proposal bound to the base
manifest digest, applied under a ledger so an interrupted apply is resumed and out-of-scope edits are refused. The digest
leaves out runtime `meta` (4), so a tick between `propose` and `accept` no longer invalidates a reviewed proposal; a proposal
reviewed under 2.x was bound to the old digest and must be proposed again. While a human proposal is reviewed and not applied,
`_dag.pending_human_amendment` is set and defect routing only queues (7.5); the marker clears itself once the file is gone,
applied or recorded as the last amendment. Known limits: the acceptance outer loop's inner ticks do not route defects (a report
from an increment node stops with exit 20 and is routed by the next `dag run`), and the system-amendment file is local.

### 7.7 Batch fix 2026-11-13: Run failure classes and the delivery guard

Two failures of one APA run (a Claude 5-hour quota stop, then three accepted submits that never advanced the node) share one mechanism: what a
terminal Run and a Worker's re-submission *mean* was judged from text and from a head compared against the wrong delivery.

* **One Run failure classification** (`decide_runs.classify_run_failure`): `capacity` (quota / rate limit / provider capacity), `transient`
  (transport; bounded by `TRANSIENT_RUNTIME_MAX_RUNS`) or `nonretryable` (a person decides). The adapter maps the platform's `failure_reason` to
  the neutral `AgentRunObservation.failure_class`; the message text is only a fallback, and a text match never overrides a transient signature.
  Worker (`loop._collect_worker_handoff`) and Reviewer (`decide_reviewer_run` → `RunAction.CAPACITY_FAILURE`) use the same classification.
* **Capacity is waited out, not counted** (`decide_runs.decide_capacity_wait`, pure): the wait ends at the reset time the error states
  ("resets 12:10pm (UTC)", an ISO instant, "retry in N seconds") plus a 60 s margin, else after a doubling backoff (300 s … 1800 s) from the failed
  Run. Nothing is persisted: the decision is a function of the Run facts and `now`, so a restarted runner reaches the same answer. While waiting,
  the node stays `in_progress`, no bounce, audit round or Run budget is spent and the tick continues with other nodes (`capacity_wait` event, once per
  Run). The only bound is time: when the stated reset (or the waiting so far) passes `retry.capacity_wait_hours`, or the Run has no usable
  timestamps, the node stops with `capacity-wait-exhausted` and the usual `omac node retry` command.
* **A verdict that judged an earlier delivery** (`WorkerHandoffIntent.source_review_superseded`, `decide_retry.retry_verdict_superseded`): an
  operator retry used the head of the *last submitted* delivery as "the rejected head". When a machine gate (CI) had already bounced the rework,
  that head was never reviewed, so an unchanged head could not satisfy the guard and every accepted submit was counted as a no-delivery Worker bounce
  until `retry.worker` was spent. A retry whose prior handoff was an explicit authoring dispatch (or itself superseded) and whose live verdict is
  gone now requires only the fresh verification attachment (`handoff_has_new_delivery`); a live `reject` still requires a moved head.
* **A submission the guard can only judge "not new" is refused at submit** (`decide_worker.head_proves_new_delivery`, `dispatch._refuse_unmoved_head`):
  the head rule is one pure function shared by the Loop and `omac work submit`. While a handoff intent is live, a submit whose PR head cannot
  count as the delivery the handoff waits for (a rejected / operator-retried head resubmitted unchanged, typically a merge conflict the Worker did
  not rebase) is rejected with the reason and the way out (push a new commit or rebase; `omac work block` if no change is possible). Nothing is
  written, so the Worker retry budget is no longer spent silently on "accepted but not a delivery"; the budget is consumed only when a Run really
  ends without a deliverable submission.
#### 批量修复 2026-11-13：决策门的自动证明与 amendment 接管

Two explicit-decision gates asked a person for something the code could already know or already had the answer to. Both
follow one rule: a gate may stop for a person only when the fact it needs is neither provable from observed platform facts
nor already answered by a later explicit decision. The report then names what is missing.

*Legacy Worker handoff (`legacy-worker-handoff-migration-unproven`).* An item that predates handoff identities has Runs but no
`worker_handoff`, so the Runs alone cannot say which one delivered; the Loop used to block every such item that already held
facts. `pipeline/decide_legacy.py::decide_legacy_delivery` (pure) now adopts the delivery when all of these hold: the platform
gives stable Run identities; every direct Run is the node's Worker's; there is no review/rework history (bounces, review
projection, reviewer baseline, obligations, ledger, continuation, sealed identity) and no operator `retry_note`; the direct Run
history fits the Run baseline cap; the item has a complete delivery (PR URL, submitted head, verification attachment) whose
attachment equals the projected verification and whose PR still has the submitted head; and exactly one *completed* Worker Run
produced the attachment (`_attachment_is_causal_for_run`: `task_id` match, else upload inside the Run's window). Adoption
persists a causal `worker_handoff` (gate `explicit-dispatch`, `target_run_id` = that Run, every other direct Run in the
baseline) first, then marks the item `DONE` as `work submit` would and comments; the normal handoff observation then seals the
delivery identity with all its existing checks, so adoption adds no weaker path. It is logged as `legacy_delivery_adopted`.
Otherwise the item blocks as before, and `decision_required.missing_proofs` lists each failed proof (`foreign-direct-run`,
`prior-review-or-rework-facts`, `operator-retry-note`, `run-history-exceeds-baseline-cap`, `platform-run-identity-not-stable`,
`no-complete-delivery`, `verification-projection-mismatch`, `pr-head-moved-after-submit`,
`no-completed-run-produced-the-verification`, `several-runs-could-have-produced-the-verification`). A crash between the intent
and the status write leaves an intent on an in-progress item, which the ordinary handoff path treats as a Worker that ended
without submit (one bounce, never a replay). Not changed: rework deliveries (review history present) still need a person.

*Amendment and a held worker decision (`validate_affected`).* A stage recovery in `authoring` or `review` resets the work item's
review state, which includes `decision_required`; so a node an amendment resumes (explicitly, or derived because its worker,
`blocked_by`, gate or implementation-affecting contract changed) has its worker decision retired on accept anyway, and refusing
the amendment at admission only forced `retry`, `hold`, propose, accept, `release`. `core/amendment.py::decision_answering_nodes`
now defines the set of nodes whose recovery answers a decision, and `validate_affected` admits a `worker-decision-required` on
exactly those nodes (at `propose` and at `accept`). Decisions on nodes the amendment only removes, or recovers at `merging`
(which does not reset review state), are still refused with a message naming the fix. The takeover is recorded before it
happens: `_prepare_apply_ledger` writes `superseded_decision` (reason code, gate, blocker reason, SHA-256 of the decision) into
the node's apply-ledger entry in the same atomic manifest save as the amended definition, ahead of the Store recovery, so a
crash resumes from the ledger and nothing is replayed. Defect-reporter nodes keep their separate rule (7.5).

*Amendment identity (`amendment-identity-conflict`).* The identity is `amend-<manifest stem>`, so every later proposal on the
same manifest meets the historical issue and used to exit 20 until a person added `--new-attempt --supersedes-issue-id`.
`pipeline/amendment.py::superseded_amendment_problem` (pure) already defined "terminal" for `--supersedes-issue-id`
(Reviewer-pass confirmation or failed-closed decision, no active or unknown Run); `propose` now applies it to the existing
issue itself and, when it holds, proceeds as `--new-attempt --supersedes-issue-id <that issue>` (same ref, same admission).
A live issue still exits 20 with the copyable command. Not changed: `--resume-issue-id` flows and the PlatformError
conflict path.

## 8. Acceptance documents

`<name>.acceptance.yaml` lists flows and the evidence that proves them; contracts map claims, contributions and refs to
them (`core/acceptance_responsibility.py`). The Reviewer maps every claim to a concrete business test.

`omac.acceptance/v3` adds `requirements` (`core/requirements.py`: id, design `source`, `summary`, `disposition`
`in-scope|out-of-scope|deferred`, `verification` `action|technical`) and `action.requirement_ids`. Code checks only ids:
requirements are unique, `requirement_ids` name known in-scope action requirements, every such requirement is referenced by
an action, and every `technical` requirement is claimed by a node's `contract.requirement_refs`. Completeness of the
extraction is the Reviewer's `requirements:extraction` obligation; the interface matrix of a decomposition is its
`interface:matrix` obligation (generated when any `requires` exists).

A flow's evidence is only as good as the interfaces between nodes, so 3.0 makes them checkable (10.1). Known limit: the
requirement checks verify ids and coverage, not whether a `ref` path exists in the pull request.

## 9. Knowledge distribution

`omac guide ...` serves bilingual, packaged, machine-consumed documents (`src/omac/guide/`), and agent role templates
live in `src/omac/agents/`. They state mechanism only (what to submit, in what shape), never project policy.

## 10. Domain model

### 10.1 Node, work item, contract

A manifest *node* (`core/manifest.py`) is a definition plus runtime state. Its *work item* is the platform issue
(`core/workitem.py`), carrying phase, verdicts, attachment references and the handoff intents. A *contract* states the
objective, acceptance, non-goals, verification commands, integration gates, `pr_base` and coverage gate.

3.0 adds typed interfaces to the contract and the artifact lists (`core/manifest.py`, checked by `core/interfaces.py` in `lint`
and `lint_increment`): a produced artifact may declare `provides: [{id, ref}]`, a consumed one `requires: [<id>]`; every
`requires` must be in the producer's `provides`, and `meta.registries` names a guard node that must run after every node that
`registers` into it (a registrant added after the guard is done is refused with the guard node to add). Contracts may also
carry `requirement_refs` (acceptance v3 technical requirements, 8) and `defect_refs` (set on the fix nodes the Loop inserts; carried, not validated). Review
obligations follow: `interface:<artifact>:<id>`, `registry:<id>`, and one aggregate `trace:refs` for nodes with
`acceptance_refs`. A `pass` for an interface-chain obligation must name a locator (a path with an extension or a test name);
only the format is checked. Fields that are absent are not written, so a manifest without 3.0 fields round-trips byte for byte.

### 10.2 Terminology

Use *result collection* (`collect_results`, 结果收集), *running nodes* (`running_nodes`, 进行中节点) and *ready nodes*
(`ready_nodes`, 就绪节点) in identifiers, errors, guides and documents.

Node status classes are defined once, in `core/graph.py`: `RUNNING` (`in_progress`, `ci_check`, `in_review`, `merging`),
`NEEDS_DECISION` (`blocked`, `failed`; `blocked` returns to `todo` on `omac node retry`, so it is not terminal) and
`SATISFIED` (`done`, `abandoned`). The DAG converges when no node is `RUNNING` or `NEEDS_DECISION`. A held node is a
`todo` node with a flag, not a status: it is not ready and satisfies nothing; a DAG with only held work left is not
converged (7.5).

## 11. Testing architecture

| Layer | What |
|---|---|
| Unit | Pure `decide_*` tables, state store, schemas, lint, projections over synthetic work items, and adapter parsing over canned platform-CLI output (only the platform CLI call is replaced). There is no in-memory platform double: anything that needs platform behaviour is a Docker test |
| Integration | Real git, a real local HTTP server, subprocesses, 145-node scale cases (`@pytest.mark.integration`) |
| Docker | `tests/it`: a private Multica (compose project `omac-it-*`, label `omac.it=1`, loopback port, `down -v`) |
| Smoke | `tests/smoke`: a real Coding Agent (Codex or Pi on the self-hosted runner) on the same stack, by hand only, never for pull requests |

Unit tests are small and fast because of this architecture (pure decisions, ports at the boundary), not because of a
per-test metric; the markers only select what runs.

The Docker layer has three fixtures, all under `tests/`: a **scripted agent** (a `claude` the real Multica daemon launches;
steps from a scenario file; it runs `omac work submit` like a real agent), a **`gh` shim** (pull requests in a JSON file,
every call logged), and a **fault proxy** between `omac` and the backend (`fail_before`, `drop_after`, `kill_after`).
`tests/it/test_it_crash_matrix.py` enumerates every write a one-node DAG makes and, for each, kills `omac` or loses the
response after the server applied it, then restarts: one live issue, at most one Run per explicit authorization, convergence.

**CI shape and timeouts.** On a 10-core development Mac the unit and integration tests take 21 s (18 CPU-s) and the 789 Docker
tests 9 min 38 s with `-n 4` (about 25 CPU-minutes). A 2 vCPU runner cannot do 25 CPU-minutes in 10 minutes, so `ci.yml` runs
the non-Docker tests (Python 3.10 and 3.12, 10-minute timeout) and the Docker tests as three parallel shards on Python 3.12
(`pytest tests/it --shard I/3`, every third file by path; one shard measured 6 min 17 s with `-n 2`, 7.2 CPU-minutes,
timeout 25 minutes). The setup a maintainer must do on GitHub is listed in `docs/github-setup.md`.

Docker is required for `tests/it` and is never skipped silently.

## 12. Engines

### 12.1 Three ports

* `WorkItemStore` (`engines/store.py`): work items, metadata, attachments, assignment, locks, control observation.
* `CodeHost` (`engines/codehost.py`): `observe_pull_request`, `request_pull_request_merge`, `check_pull_request`,
  `read_pull_request_readiness`. Commands configured for checks and merges run only here.
* `AgentRuntime` (`engines/runtime.py`): `wake`, `list_runs`, `cancel`, provisioning, capabilities.

`Engine(store, runtime, code_host)` combines one adapter per port; the pipeline receives `code_host` explicitly.

### 12.2 Assembly

`engines.engine_from_config` is the only way the CLI builds an engine (config < `OMAC_*` < flags);
`create_engine` is the only factory and the only importer of concrete adapters. `engine: mock` was removed in 2.0 and is
reported with the fix.

### 12.3 Multica mapping

Issue = work item; typed values live in issue metadata (bounded; large text is an attachment referenced by digest);
direct Runs carry an attribution (`issue_assignment`) the controller treats as formal; the `multica` CLI is used for all
writes, with exact-UUID `curl` as a documented fallback for atomic projection updates.

Issue metadata has a platform-wide 8KB aggregate limit. Every metadata write goes through `MulticaStore._set_metadata`; when the
platform rejects it (or a control reference does not fit), `_free_metadata_budget` makes room in a fixed order: recoverable
reductions (compacted baseline run ids, obligations, source refs), then inline `delivery_identity` / `reviewer_run_baseline` /
`worker_handoff` become `omac.control-payload/v1` attachment references (read back by `_json_control_metadata`). If that is not
enough the write fails with `MetadataBudgetError` (`platform-metadata-budget`). `source_refs` grows with a node's upstream
count, so above 2KB it always becomes an attachment reference (same wrapper, same reader; inline values written by older
versions still read). A per-value overrun (`assert_metadata_write_allowed`) is also a `MetadataBudgetError`: the Loop blocks
only that node (`metadata-budget` comment with `omac node retry`) and keeps dispatching the others. A Reviewer submit that fails this way leaves a
comment (`core/submit_rejection.py`, outside the metadata budget); the Loop blocks the stage with
`reviewer-submit-rejected-by-platform` rather than counting a no-submit Run (§7).

### 12.4 Platform boundary

Pipeline, core, cli and web never execute a platform CLI and never depend on its command syntax or on Agent transcript
formats. If a fact is needed, add a semantic method to a port and let the adapter return structured data. Wording of port
methods stays platform-neutral.

## 13. Web

### 13.1 Read-only

`omac web` serves a local dashboard. It has no write path.

### 13.2 API

The routing layer parses parameters, calls a command function, and returns its JSON unchanged. It does not read
manifests, call engines, transform data or swallow exceptions. When the web needs data no command returns, add a CLI
command with `--output json` first, so humans, agents and the web see the same facts.

### 13.3 Static page

The SPA polls `/api/meta` and renders manifests, node evidence and the acceptance checklist from the API only.

## 14. Batch fix 2026-11-13: paths and branch references in persisted content

**Mechanism.** Everything written to the manifest, `_dag.yaml`, node state files or a commit message is shared through git and
may be published, so it must not depend on the machine that wrote it. `core/pathrefs.py` is the single exit for paths:
`persist_path(path, manifest_path)` returns a POSIX path relative to the repository root (the parent of `.omac/`), or the bare
file name for a file outside the repository; `resolve_path(value, manifest_path)` opens a persisted value (relative values
against the root; a legacy absolute value as recorded, and when it no longer exists, the same relative location or its name
next to the manifest). Writers: `pending_human_amendment.file` (`pipeline/amendment.py::_write_reviewed_amendment`), the apply
ledger `amendment_file` (`core/amendment.py::_apply_new_amendment`), the system-amendment intent `defects.applying.file`
(`pipeline/defects.py`), and the config-sync commit subject and `config_synced` log (`core/gitsync.py::ensure_config_synced`).
Readers resolve through `resolve_path`. Not rewritten: old absolute records stay until their next natural rewrite (they are
read correctly, and the ledger is transient). CLI stdout (`amendment_file`, `next_action`) keeps the path the caller typed.

**Branch.** The amendment authoring contract hard-coded `pr_base="manifest-runtime"`, a branch that exists nowhere, so the
Orchestrator tried to check it out to read the manifest and returned empty-handed. `pipeline/amendment.py::_manifest_branch`
now takes the DAG's integration branch (`meta.pr_base`, else the nodes' single `pr_base`, else `main`).

**Not changed.** Work-item task arguments (`dispatch.py`, `plan_file`/`manifest_file`…) are platform-side and not committed.

# oh-my-multica design (2.0)

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
   blocks and `omac` exits 20 with a structured report. `omac node retry|abandon|accept` are the decisions.
5. **Unknown results are never replayed; drift fails closed.** If a write may or may not have happened, `omac` persists an
   intent *before* the effect, and after a crash it observes instead of repeating. If the platform no longer matches what
   was recorded, it stops and says so.
6. **One writer per manifest**, guarded by a lock; the manifest is small by construction (4).

## 3. Structure and layering

```
web → cli → pipeline → engines → core → i18n → errors
```

| Layer | Role |
|---|---|
| `web` | Read-only dashboard. Parses parameters, calls command functions, returns their JSON unchanged (13). |
| `cli` | Argument parsing, exit codes (`cli/exit_codes.py`, `sys.exit` only in `cli/main.py`), engine assembly. |
| `pipeline` | The Loop: reconcile, result collection, gates, dispatch, delivery, acceptance, plan, amendment. |
| `engines` | Ports (`store.py`, `runtime.py`, `codehost.py`) and adapters (`multica.py`, `github.py`). |
| `core` | Domain model, manifest I/O, validation, evidence, graph, git state sync. No platform knowledge. |

Enforced by `lint-imports` (contracts in `pyproject.toml`, function-level imports count) and by
`tests/test_architecture.py`: platform CLIs (`multica`, `gh`, `curl`) run only inside adapters; `git`, `bash -n` and
`lsof` are the only other subprocess users, on an explicit allowlist; `decide_*` modules import neither `engines` nor
`subprocess`; `create_engine` is called only inside `engines`. The single permanent layering exception is the `omac web`
command starting the web server.

## 4. State storage

A DAG is two things on disk, both plain text, both committed to git:

```
.omac/<name>.yaml            DAG definition: meta + node definitions. Changes only on plan and amendment.
.omac/<name>.state/<node>.yaml   One file per node: work_item_id, status, merge facts. A few hundred bytes.
```

* **Incremental atomic writes.** `save_manifest` compares each file's content with what the last load/save left on disk
  and rewrites only what changed: write a temporary file in the same directory, `fsync`, `os.replace`. Node state files
  are written first and the definition file last, so a record never claims progress that is not durable. A killed
  process leaves every file either old-and-whole or new-and-whole.
* **Integrity.** Each state file starts with `# sha256: <digest of the body>`. A mismatch (hand edit, truncation, bad
  merge) is refused with the file path and `git checkout -- <file>`.
* **Sync through `origin/main`.** `core/gitsync.py` commits the definition and the state directory after each tick.
  Machines advancing different nodes touch different files, so git merges them without conflict; a conflicting push
  is rebased only when every unpushed commit touches nothing but those files.
* **Bounded size.** The definition holds no run history; the amendment ledger in `meta` is replaced per amendment.
  Large payloads (captured sources, transcripts) are attachments on the platform, referenced by digest.
* **No 1.x compatibility.** A single-file 1.x manifest (inline `work_item_id`, or the old compressed envelope) is not read
  or modified: the error says to finish that run with 1.x or abandon it, then run `omac plan create` with 2.0.
* **Node ids** become file names: letters, digits, `.`, `_`, `-`.

## 5. CLI contract

### 5.1 Exit codes and errors

| Code | Meaning |
|---|---|
| 0 | The DAG converged (nothing running, blocked or failed; `done`, `abandoned`, `cancelled` count) and, when required, final acceptance passed. |
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
`{failed_nodes, blocked_downstream, next_actions}`, and `next_actions` are copyable commands.

## 6. Configuration

`.omac/config.yaml` (precedence: file < `OMAC_*` environment < flags): `engine: multica`, `workspace`, `project`
(required: every issue belongs to a project), `roles` (planner, orchestrator, workers, reviewers, acceptor),
`defaults` (`max_parallel`, `poll_interval`, `coverage_gate`), `workflow` (human-in-the-loop, review, acceptance doc),
`ci.check_command`, `merge.command` (default `gh pr merge {pr_url} --squash --delete-branch`),
`retry` (`worker|ci|review|merge`, `no_submit_runs`), `reconcile`, `acceptance.max_rounds`. `omac init` writes it;
the engine prompt defaults to `multica`.

## 7. The Loop

### 7.1 init

Writes the config, picks or creates the platform project (the engine prompt defaults to `multica`), and offers to
provision agents from the packaged templates.

### 7.2 plan

`plan create|resume` runs a pipeline of authoring tasks (design, decomposition, review) through the same
produce → machine gate → bounded review loop as any node, then writes the manifest and the acceptance document and
commits them together.

### 7.3 dag run

One tick:

```
reconcile → result collection (collect_results) → evidence / CI / merge gates → ready nodes → dispatch → converge or exit 20
```

* **reconcile** reads the *running nodes* (进行中节点) and platform facts, repairs manifest/platform drift, and runs a
  low-frequency full audit.
* **result collection** walks each running node through staged functions (`worker blocker`, `handoff convergence`,
  `worker handoff`, `review phase`, then `in_progress | in_review | merging` collectors).
* **Pure decisions** live in `pipeline/decide_merge.py`, `decide_worker.py` and `decide_review.py`: facts in, a named
  action out, table-tested without a platform. The shell reads, decides, persists the intent, applies one effect.
* **Merge delivery** is a state machine on `merge_request_state` (`None → intent → requested → observed MERGED`). A marker
  is never followed by a second merge request; after an unknown outcome the node stops (exit 20) and `omac node retry`
  is the explicit decision. Success is concluded only from the *observed* pull request, never from the command's exit code.
* **ready nodes** (就绪节点) are nodes whose blockers are `done` or `abandoned`.

### 7.4 Stage handoff

Worker and Reviewer turns are chained by persisted intents so a restart resumes instead of repeating: a Worker handoff
binds the target agent, the Run, a baseline of earlier Runs and the verification attachment; a submit is accepted as a
delivery only if the Run, the attachment and the pull-request head line up (`delivery_identity`, sealed by the
controller). The Reviewer is dispatched against a subject digest; a verdict applies to that subject only. A rejected
delivery returns to the Worker within the review budget and must move the pull-request head.

### 7.5 Dependencies and abandonment

A node is ready when every blocker is `done` or `abandoned`. `node abandon` turns a blocked node into `abandoned` and
unlocks its downstream. `node accept-nits|accept|retry` are the other explicit decisions.

### 7.6 Acceptance and amendments

When the DAG converges and the manifest requires acceptance, an outer loop runs the acceptor against the acceptance
document (`acceptance.max_rounds`, independent of `retry`). Failed flows produce an incremental decomposition that is
merged into the manifest. `dag amend propose|accept` changes a running DAG: a reviewed proposal bound to the base
manifest digest, applied under a ledger so an interrupted apply is resumed and out-of-scope edits are refused.

## 8. Acceptance documents

`<name>.acceptance.yaml` lists flows and the evidence that proves them; contracts map claims, contributions and refs to
them (`core/acceptance_responsibility.py`). The Reviewer maps every claim to a concrete business test.

## 9. Knowledge distribution

`omac guide ...` serves bilingual, packaged, machine-consumed documents (`src/omac/guide/`), and agent role templates
live in `src/omac/agents/`. They state mechanism only (what to submit, in what shape), never project policy.

## 10. Domain model

### 10.1 Node, work item, contract

A manifest *node* (`core/manifest.py`) is a definition plus runtime state. Its *work item* is the platform issue
(`core/workitem.py`), carrying phase, verdicts, attachment references and the handoff intents. A *contract* states the
objective, acceptance, non-goals, verification commands, integration gates, `pr_base` and coverage gate.

### 10.2 Terminology

Use *result collection* (`collect_results`, 结果收集), *running nodes* (`running_nodes`, 进行中节点) and *ready nodes*
(`ready_nodes`, 就绪节点) in identifiers, errors, guides and documents.

## 11. Testing architecture

| Layer | What | Budget per test |
|---|---|---|
| Unit | Pure `decide_*` tables, state store, schemas, lint, adapter parsing, and orchestration over an in-memory port double (`tests/_support/fake_platform.py`, never shipped) | 0.2 s target, 0.4 s gate, 300 MB RSS, 10 MB tmp |
| Integration | Real git, a real local HTTP server, subprocesses, 145-node scale cases (`@pytest.mark.integration`) | 5 s |
| Docker | `tests/it`: a private Multica (compose project `omac-it-*`, label `omac.it=1`, loopback port, `down -v`) | 30 s, 500 MB |
| Smoke | `tests/smoke`: a real Coding Agent on the same stack, weekly, never for pull requests | minutes |

The Docker layer has three fixtures, all under `tests/`: a **scripted agent** (a `claude` the real Multica daemon launches;
steps from a scenario file; it runs `omac work submit` like a real agent), a **`gh` shim** (pull requests in a JSON file,
every call logged), and a **fault proxy** between `omac` and the backend (`fail_before`, `drop_after`, `kill_after`).
`tests/it/test_it_crash_matrix.py` enumerates every write a one-node DAG makes and, for each, kills `omac` or loses the
response after the server applied it, then restarts: one live issue, at most one Run per explicit authorization, convergence.

Per-test limits (`tests/_support/budget.py`) are enforced by `tests/conftest.py`; `EXEMPT_FILES` is empty. Docker is required
for `tests/it` and is never skipped silently. The smoke run's spend is capped at 20 USD per month: a metered key with a
hard limit in the provider console is the main gate, `tests/smoke/ledger.py` refuses to start at the cap.

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

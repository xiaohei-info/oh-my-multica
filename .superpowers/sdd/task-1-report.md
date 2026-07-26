# Task 1 implementation report

## Core judgement

✅ Worth doing: `done` is an integration-closure fact, so a merge command exit code
cannot be treated as proof that a develop-node PR reached the target branch.

- Data ownership: the engine adapter owns the platform CLI request/observation;
  pipeline and CLI consume only `WorkItemStore` plus `AgentRuntime`.
- Simplification: one remote PR observation state drives all completion decisions;
  no-review and reviewed delivery now share the same merge closure.
- Main risk contained: historical `done` records without confirmed merge are no
  longer allowed to unlock dependants or report DAG convergence.

## TDD record

1. Added the three focused regressions before production edits:
   - `reviewer=None` cannot become `done` without confirmed merge.
   - merge command exit `0` with an observed remote `OPEN` PR cannot unlock a
     dependant or converge the DAG.
   - observed `MERGED` uses the authoritative remote `mergedAt` value.
2. RED command:

   ```sh
   PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
     tests/test_delivery_merge.py::TestMergeClosureRegression -q
   ```

   Expected RED was captured as `3 failed`: no-review direct completion,
   missing remote observation/downstream dispatch, and a locally generated
   timestamp instead of the authoritative remote timestamp.
3. Implemented only after RED. The same focused regression command then passed.

## Changes

- Added typed `PullRequestState`, `MergeCommandResult`, and
  `PullRequestObservation` contracts.
- Added `request_pull_request_merge` and `observe_pull_request` to
  `WorkItemStore`; Mock and Multica adapters own the merge command and remote
  `gh pr view` observation.
- Changed merge closure semantics:
  - only `MERGED` with `mergedAt` sets `merged`, `merged_at`, `done`,
    `node_done`, convergence, or downstream readiness;
  - `OPEN` stays in `merging` without bouncing a worker merely for queue/auto-
    merge delay;
  - a command failure is observed before retry; a known open command failure
    follows the existing bounded worker bounce, while unknown observations fail
    closed;
  - `CLOSED_UNMERGED` uses the existing bounded rework path.
- Removed the no-review direct-to-`done` branch and routed it through the same
  closure.
- Reconciled historical develop `done` states with PRs: backfill confirmed
  merge facts, re-enter `merging` for open PRs, and block closed-unmerged or
  unobservable outcomes. This path does not cancel active Agent Runs or create
  a replacement PR/work item.
- Guarded `omac node accept` so an unconfirmed develop PR cannot be converted
  to `done`; explicit abandonment remains separate.
- Updated old fixtures that represented a PR-bearing `done` node without merge
  facts as valid completion.

## Test and verification record

- Focused GREEN:

  ```sh
  PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
    tests/test_delivery_merge.py tests/test_cli_node.py tests/test_loop.py \
    tests/test_events_tick.py -q
  ```

  Passed.
- Full suite (fresh, before commit):

  ```sh
  PATH="$PWD/.venv/bin:$PATH" python3 -m pytest tests/
  ```

  Exit code `0`; 11 `live` tests were skipped by the repository's default
  marker policy.
- `git diff --check` completed with no output.

## Scope and safety

- No OAC repository files were read or modified.
- No PR was merged or mutated; all tests used mock/example PR URLs.
- No active Agent Run cancellation was introduced.

## Review-fix follow-up

### RED

Added focused regressions before changing production code for:

- platform read failure versus proven work-item absence;
- develop `done` without a PR, and `merged=true` without `merged_at`;
- historical open PR issuing a merge request;
- persisted merge intent before the external request;
- command timeout followed by remote pending/queued state;
- `node accept` without PR or without authoritative merge timestamp;
- `merging` status reporting; and
- Multica remote observation classification.

RED command:

```sh
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression \
  tests/test_cli_node.py::test_accept_rejects_develop_node_without_pr \
  tests/test_cli_dag.py::TestBuildStatusReport::test_merging_node_counts_as_running \
  tests/test_engines_multica.py::test_multica_observe_pull_request_classifies_remote_states \
  tests/test_engines_multica.py::test_multica_observe_pull_request_fails_closed_for_unreadable_remote -q
```

Captured RED: 12 behavior failures covering every requested gap (the first
attempt exposed the missing `PENDING` enum at collection time; the test was
then adjusted to produce assertion-level RED).

### Fixes

- Added `WorkItemNotFoundError`; reconciliation now clears/recreates only a
  proven missing item. Other read failures block unconfirmed nodes and retain
  their identity.
- Develop `done` now requires a PR plus confirmed merge facts with a non-empty
  authoritative timestamp. `node accept` always re-observes the develop PR;
  no-PR and stale local flags are rejected.
- Added persisted `merge_request_state: requested`. Pipeline observes before
  every merge request, atomically saves the intent before the external call,
  then observes again. Historical ordinary-open PRs enter this request path.
- Added `PENDING` for auto-merge / merge-queue observations. Pending remains
  `merging`; timeout/failure plus pending does not bounce a worker. Unknown
  remains fail-closed.
- Multica observation now requests and classifies `autoMergeRequest` and
  `isInMergeQueue`, with direct adapter tests for merged, open, pending,
  queued, closed-unmerged, CLI/auth failure, timeout, and malformed JSON.
- `report.py` now counts `merging` (and `ci_check`) as running.
- Updated legacy test fixtures that represented develop `done` without merge
  facts; preserved `RuntimeError` compatibility for mock missing-item callers.

### GREEN and verification

Focused GREEN:

```sh
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest \
  tests/test_delivery_acceptance.py tests/test_engines_mock.py \
  tests/test_delivery_merge.py tests/test_cli_node.py tests/test_cli_dag.py \
  tests/test_engines_multica.py tests/test_loop.py tests/test_events_tick.py -q
```

Passed.

Full suite:

```sh
PATH="$PWD/.venv/bin:$PATH" python3 -m pytest tests/
```

Result: `858 passed, 11 skipped` in `225.46s`, exit code `0`.
`git diff --check` passed.

Follow-up commit: `cbfe084 fix: harden merge closure recovery`.

## Second re-review follow-up

### RED

The persisted merge-intent/no-work-item/unsupported-gh focused RED run exposed
11 failures before the production fixes. The subsequent full-suite run exposed
the remaining fixture-only failures under the stricter develop closure rule:
eight acceptance tests lacked the `WorkItemStatus` import used by their new
PR-bearing WorkItem helper, and the abandon e2e expected a fresh in-memory
mock process to retain unprovable develop `done` state.

The abandon reproduction was:

```sh
.venv/bin/python -m pytest \
  tests/test_e2e_p1.py::TestAbandon::test_abandon_unlocks_downstream -q
```

It failed only after the second CLI process ran `dag status`: the preceding
`dag run` returned `converged`, but the new process had no mock WorkItem/PR
facts and correctly fail-closed the historical develop `done` nodes.

### Fixes

- Develop `done` now fail-closes when it has no `work_item_id`, a proven
  missing WorkItem, or an unreadable platform record; `abandoned` remains an
  explicit separate dependency-satisfying state.
- `node accept` rejects a node without a WorkItem; explicit `abandon` remains
  the recovery path. `node retry` clears an unresolved merge request marker.
- Merge delivery now persists `intent` before the side effect and `requested`
  after it returns. A reloaded OPEN PR with `requested` is observed but never
  requested again; an unresolved `intent` blocks with a clear manual recovery
  message rather than duplicating an external request.
- Multica now requests only supported local gh fields:
  `state,mergedAt,autoMergeRequest,mergeStateStatus`. Auto-merge and QUEUED
  classify as pending; absent/UNKNOWN merge state fails closed. A local
  `gh pr view --help` contract test guards the requested field list.
- Acceptance fixtures now create PR-bearing mock WorkItems for historical
  develop completions. The abandon e2e verifies the converged run result and
  persisted manifest rather than asking a fresh non-persistent MockStore to
  prove remote merge facts.

### GREEN and verification

Focused GREEN:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_acceptance.py \
  tests/test_cli_dag.py tests/test_cli_node.py tests/test_delivery_merge.py \
  tests/test_engines_multica.py tests/test_loop.py \
  tests/test_e2e_p1.py::TestAbandon::test_abandon_unlocks_downstream -q
```

Result: `196 passed`.

Full suite:

```sh
.venv/bin/python -m pytest tests/ -q
```

Collected: `874`; exit code `0` (`863 passed, 11 skipped`).
`git diff --check` passed.

Third follow-up commit: `edca2ac fix: close develop merge recovery gaps`.

## Third re-review follow-up

### RED

Added focused regressions first for a malformed persisted merge marker, Store
PR-operation contracts, Multica adapter ownership, pipeline CI delegation, and
programming-error propagation from `reconcile`.

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_malformed_merge_request_state_fails_closed_without_request \
  tests/test_delivery_ci.py::TestCollectResultsCi::test_github_workflow_defaults_to_ci_before_review \
  tests/test_cli_work.py::TestSubmitPerKindPhase::test_develop_authoring_rejects_github_draft_pr_atomic \
  tests/test_init.py::test_pr_operations_are_part_of_store_interface \
  tests/test_engines_multica.py::test_multica_pr_check_and_readiness_stay_in_adapter \
  tests/test_loop.py::test_reconcile_does_not_swallow_programming_errors -q
```

Initial RED: six failures covering the malformed state external merge attempt,
missing Store capabilities, pipeline CI bypass, and broad exception swallowing.

### Fixes

- `run_merge_delivery` now accepts only `None`, `intent`, or `requested` as a
  persisted merge request state. Any other value blocks before PR observation
  or merge request, records the safe recovery (`verify remotely`, then
  `omac node retry`), and cannot settle done.
- Added `WorkItemStore.check_pull_request` and
  `WorkItemStore.read_pull_request_readiness` with explicit typed results.
  MockStore and MulticaStore own command execution/PR reads; delivery and
  dispatch now only call the Store. Configuration command overrides and
  existing validation messages remain intact.
- `reconcile` now catches only `PlatformError`/`AuthError` after the explicit
  not-found case, so programming errors propagate instead of being mislabeled
  as platform state.
- Migrated test doubles from the removed pipeline subprocess location to the
  Mock adapter boundary.

### GREEN and verification

Focused GREEN:

```sh
.venv/bin/python -m pytest \
  tests/test_dispatch.py tests/test_delivery_ci.py tests/test_delivery_merge.py \
  tests/test_cli_work.py tests/test_engines_mock.py tests/test_engines_multica.py \
  tests/test_init.py tests/test_loop.py -q
```

Passed. `git diff --check` passed.

Full suite:

```sh
.venv/bin/python -m pytest tests/ -q
```

Collected: `873`; exit code `0` (`862 passed, 11 skipped`).
Fourth follow-up commit: `7364150 fix: enforce merge state and adapter boundaries`.

## Fourth re-review follow-up

### RED

Added a historical `done` recovery matrix for malformed/intent/requested markers
against MERGED/OPEN/PENDING, a timeout+OPEN restart de-duplication regression,
Mock merge/check result parity tests, and readiness malformed/unknown-result
adapter and dispatch tests.

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_historical_done_preserves_merge_marker_closure \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_timeout_with_open_pr_preserves_intent_across_restart \
  tests/test_engines_mock.py::test_failed_merge_or_check_is_not_synthesized_as_success \
  tests/test_engines_mock.py::test_mock_merge_requires_explicit_auto_merge_configuration \
  tests/test_engines_multica.py::test_multica_readiness_malformed_payload_fails_closed \
  tests/test_cli_work.py::TestSubmitPerKindPhase::test_develop_authoring_rejects_unknown_readiness_result -q
```

Initial RED exposed missing tagged readiness types plus marker recovery that
cleared/re-entered the merge path and Mock's legacy synthesized success.

### Fixes

- Historical develop `done` recovery validates `merge_request_state` before
  cached merge facts or remote observation. Malformed values block; intent and
  requested are preserved for OPEN, promoted only by remotely observed PENDING,
  and cleared only by authoritative MERGED plus `mergedAt`.
- Timeout plus OPEN preserves `intent`, blocks fail-closed, and records a full
  recovery command with manifest path and node key. It cannot bounce or issue a
  second automatic request after restart.
- MockStore no longer treats failed `gh pr merge`/`gh pr checks` as success or
  fabricates MERGED. Explicit `MOCK_AUTO_MERGE_ON_SUCCESS=true` test/E2E
  fixtures model a successful remote merge after a successful command.
- Replaced optional readiness-field bags with tagged success
  (`PullRequestReadiness`) and failure (`PullRequestReadinessFailure` plus enum)
  results. Multica malformed/missing/untyped payloads fail closed; dispatch
  accepts only the typed success result.

### GREEN and verification

Focused GREEN:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py tests/test_delivery_ci.py tests/test_cli_work.py \
  tests/test_engines_mock.py tests/test_engines_multica.py tests/test_loop.py \
  tests/test_dispatch.py tests/test_e2e_p1.py tests/test_e2e_p4_delivery.py \
  tests/test_delivery_acceptance.py tests/test_events_tick.py -q
```

Passed. `git diff --check` passed.

Full suite:

```sh
.venv/bin/python -m pytest tests/ -q
```

Collected: `891`; exit code `0` (`880 passed, 11 skipped`).
Fifth follow-up commit: `e9e3294 fix: harden merge recovery semantics`.

## Fifth re-review restart-safety follow-up

### RED

The existing crash-after-worker-wake regression passed against the inherited
worktree, but four stricter ordering checks exposed three remaining failures:

- the initial `merging + intent` transition was written to the platform before
  it was saved to the manifest;
- `manifest_path=None` still allowed PR observation/request side effects; and
- the merge-bounce cap entered its platform writes before the durable intent
  transition was visible on disk.

Focused RED:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_intent_is_saved_before_platform_status_write \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_delivery_without_manifest_path_has_no_external_effects \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_bounce_cap_is_saved_before_platform_writes \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_closed_unmerged_is_saved_before_worker_side_effects -q
```

Result: `3 failed, 1 passed`.

A second restart regression then reproduced a stale-platform window at the
retry cap: after the manifest persisted `blocked`, a crash before the platform
block write allowed reconcile to overwrite the local failure with the stale
platform review state and repeat merge processing.

### Fixes

- `run_merge_delivery` now requires a real manifest path before it observes or
  mutates merge state; direct pathless calls fail before external effects.
- Every merge intent/pending/blocked transition is saved before the related
  platform status, comment, metadata, reset, assignment, wake, or merge request.
- Definite failure plus confirmed OPEN, and `CLOSED_UNMERGED`, share the same
  manifest-first bounded bounce. Timeout plus OPEN remains fail-closed with the
  durable `intent` marker and never enters the safe-bounce path.
- The retry-cap path persists `blocked` before platform writes. Reconcile now
  preserves persisted `blocked`/`failed` decisions against stale platform
  projections; only explicit retry or a new valid worker delivery can reopen
  them.
- Updated the old no-review test description and merge state diagram to require
  authoritative remote `MERGED + mergedAt` closure.

### GREEN and verification

Focused restart-safety GREEN:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_bounce_cap_survives_crash_before_platform_block \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_definite_merge_failure_persists_worker_bounce_before_crash \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_intent_is_saved_before_platform_status_write \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_delivery_without_manifest_path_has_no_external_effects \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_bounce_cap_is_saved_before_platform_writes \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_closed_unmerged_is_saved_before_worker_side_effects -q
```

Result: `6 passed`.

Related suite:

```sh
.venv/bin/python -m pytest tests/test_delivery_merge.py tests/test_loop.py -q
```

Result: `98 passed`.

Full suite:

```sh
.venv/bin/python -m pytest tests/ -q
```

Collected: `897`; exit code `0` (`886 passed, 11 skipped`).
`git diff --check` passed.

### Residual risk

The fifth follow-up persisted the destination state before platform writes, but
did not represent an unfinished worker handoff. A crash before reset/assign/wake
could therefore leave `in_progress` on disk while the platform remained in the
review phase. The sixth review correctly identified this statement as broader
than the implementation actually guaranteed.

## Sixth re-review durable merge-bounce follow-up

### RED

Replaced the single wake-boundary regression with a real platform fixture in
`IN_REVIEW + REVIEW + verdict=pass`, parameterized across crashes after each
bounce side effect: comment, absolute metadata write, status update, review
reset, worker assignment, and wake.

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_bounce_pending_recovers_after_each_platform_effect -q
```

Result before implementation: `6 failed`. Every failure showed that the disk
manifest had already cleared the merge marker and moved to ordinary
`in_progress`, so restart had no durable instruction to finish the handoff.

### Fix

- Reused `merge_request_state` as the single tagged closure marker. The durable
  form `bounce_pending:<absolute-attempt>` means the merge command is proven to
  have failed safely and the worker handoff is incomplete. The suffix is the
  absolute `merge_bounce` target, not another state field.
- A pending marker is saved before comment/metadata/status/reset/assign/wake.
  Restart sees it before PR observation and replays the same handoff without
  issuing another merge request.
- `merge_bounce` is written as the marker's absolute value. If the platform
  already contains that value after a partial attempt, replay does not
  increment it or repost the normal bounce comment.
- Status update, review reset, assignment, and wake use their existing
  idempotent contracts. Only after every operation returns successfully does
  OMAC save `in_progress + marker=None`.
- Retry exhaustion follows the same pending marker but settles to `blocked`;
  `CLOSED_UNMERGED` uses the safe bounce path. Timeout plus OPEN remains
  `intent + blocked` and never enters `bounce_pending`.

### GREEN and verification

Focused fault-injection GREEN:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py::TestMergeClosureRegression::test_merge_bounce_pending_recovers_after_each_platform_effect -q
```

Result: `6 passed`.

Related suite:

```sh
.venv/bin/python -m pytest \
  tests/test_delivery_merge.py tests/test_loop.py \
  tests/test_cli_node.py tests/test_manifest.py -q
```

Result: `131 passed`.

Full suite:

```sh
.venv/bin/python -m pytest tests/ -q
```

Collected: `902`; exit code `0` (`891 passed, 11 skipped`).
`git diff --check` passed.

### Remaining non-blocking items

- A process crash after a bounce comment is accepted but before the absolute
  counter write can duplicate that diagnostic comment on replay. It cannot
  duplicate the merge request, increment the counter twice, or create an
  unbounded Agent Run loop.
- Historical `done` fail-closed reconciliation may repeat a diagnostic comment
  or status write. This remains a Minor follow-up because it does not issue a
  merge request or dispatch an Agent Run.

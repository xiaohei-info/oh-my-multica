# Changelog

This file records public changes to oh-my-multica. The format follows
[Keep a Changelog], and version numbers follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

### Changed

- Directory-backed plan and amendment sources now inventory every file tracked
  by the current Git revision instead of local untracked or ignored files, so
  remote Agents can reproduce the exact authoritative docs digest.
- Review rework now stops on semantic non-convergence instead of consuming a
  large configured retry budget. The existing blocker ledger derives stalled,
  scope-expanding, and ten-cycle-exhausted decisions; infrastructure retries
  remain outside that budget, and develop nodes request a reviewed DAG amendment
  without OMAC rewriting topology automatically.
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
  blocker ledger, structured prior-blocker regression results, and automatic
  convergence-audit signaling. Legacy in-flight review reports remain readable
  until OMAC prepares their next review subject.
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
[1.0.0]: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v1.0.0

# Changelog

This file records public changes to oh-my-multica. The format follows
[Keep a Changelog], and version numbers follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

### Changed

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

# Changelog

This file records public changes to oh-my-multica. The format follows
[Keep a Changelog], and version numbers follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

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

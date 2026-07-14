# Changelog

All notable changes to **omac** are documented here. The format follows
[Keep a Changelog] and version numbers follow [Semantic Versioning].

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/

## [Unreleased]

### Added

- Project-local language configuration. `omac init` selects `en` or `cn` and
  writes `language` to `.omac/config.yaml`; projects without the key default to
  English.
- Localized `work show` protocol, authority order, table view, Guide loading,
  and top-level CLI help, while preserving JSON schema, task facts, and
  executable submit commands.
- Complete English mirrors for all packaged OMAC Guides. Chinese remains
  available through the same project setting.
- English primary README and Simplified Chinese mirror.
- `omac init` can create Multica agents from nine built-in templates. It injects
  Instructions, uploads missing Skills, and binds them to a new agent; existing
  agents are unchanged and all created agents enter the same role-mapping pool.
- Planner-authored plans now require a second reviewed `project-rules` artifact.
  OMAC persists it through the engine, updates only its managed section in the
  repository-root `AGENTS.md`, and commits it with the manifest and acceptance
  outputs. `--doc` continues to skip planner authoring and this update.

## [1.0.0] — 2026-07-05

First stable release. OMAC moved from a single agent carrying a long context to
a convergent workflow built from contracts, a manifest DAG, parallel agents,
structured evidence, and independent acceptance. The deterministic CLI owns the
orchestration loop; LLM work is reserved for planning, decomposition,
development, review, and acceptance.

### Added

- **Delivery-level end-to-end closure (§7.6 / §10.3):** `plan create` outputs a
  manifest and acceptance document; `dag run` exercises mock CI and merge;
  final acceptance can fail, add an increment, pass, and return exit 0. The
  `e2e` suite covers passing CI, merge, the acceptance outer loop, and exit-code
  paths.
- **Mock-engine stability:** `_auto_complete_check` uses real `work submit` for
  registered behavior and a generic DONE-with-deliverable fallback, preventing
  `plan create` from hanging. Review and verification safely handle dict-shaped
  acceptance and decomposition contracts.
- **Release material:** version raised to `1.0.0`, this changelog added, and
  README commands checked against runnable paths.

### Fixed

- **CI node permanently stuck in `in_progress`:** after CI pass,
  `advance_delivery` returned the work item to `IN_PROGRESS`; the no-reviewer
  path marked the manifest done without synchronizing the platform work item.
  The next reconciliation reverted it and the loop never converged.
- **Mock reviewer decision was lost:** assignment could auto-complete a work
  item while it was still `IN_PROGRESS`, clearing assignment state before the
  reviewer wake-up. The item now enters `IN_REVIEW` before assignment.
- **Mock delay in `conftest`:** `MOCK_AUTO_COMPLETE_DELAY` now defaults to 0 so
  library-level `main()` tests are fast and match subprocess e2e behavior.
- **Delayed acceptance emit in `dag.py`:** after acceptance adds fix nodes, an
  extra idempotent tick runs before emit so JSON reflects the updated done list.
- **Emit JSON schema:** `--output json` always includes `report` (`null` after
  convergence), so consumers do not need to guard field access.

### Changed

- `src/omac/__init__.py` and `pyproject.toml` moved from version `0.1.0` to
  `1.0.0`.

## [0.1.0] — 2026-06

Initial internal release with P1–P4:

- **P1 — Foundation and observability:** command tree, exit-code contract,
  multi-engine skeleton, mock engine, and `run_task` loop.
- **P2 — Pipeline and parallelism:** manifest, graph, dispatch,
  `collect_results`, mock CI, develop authoring, three-step PR closure, Web
  server, and SPA dashboard.
- **P3 — Planning and decomposition:** plan create/check/show, reviewer handoff,
  retry configuration, and README coverage.
- **P4 — Acceptance and closure:** CI monitoring with bounded fallback,
  automatic merge, conflict fallback, acceptance increment loop, lint and merge
  increment, and delivery closure.

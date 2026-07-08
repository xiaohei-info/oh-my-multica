# Guide Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize omac guide topics by workflow, lifecycle roles, artifacts, and recovery without preserving old flat role commands.

**Architecture:** Keep guide content as packaged Markdown files loaded by `omac guide`. Replace flat topic discovery with grouped commands (`role <name>`, `artifact <name>`) and update tests/docs to make the new information architecture explicit.

**Tech Stack:** Python CLI, packaged Markdown resources, pytest.

## Global Constraints

- Do not change pipeline state-machine behavior.
- Do not keep compatibility aliases such as `omac guide worker` or `omac guide planner`.
- Keep knowledge distribution inside CLI guide/help/error text; no external skill dependency.
- Use TDD: add failing tests before production code changes.

---

### Task 1: Guide CLI Topic Model

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/omac/cli/commands/guide.py`
- Modify: `src/omac/guide/__init__.py`

**Interfaces:**
- Consumes: existing `omac guide` CLI entry point.
- Produces: `omac guide role <planner|orchestrator|worker|reviewer|acceptor>` and `omac guide artifact <design|acceptance|manifest|evidence>`.

- [ ] Write failing CLI tests for grouped guide commands and rejection of old flat role commands.
- [ ] Run targeted tests and confirm failures.
- [ ] Implement grouped topic dispatch and package loader support.
- [ ] Run targeted tests and confirm pass.

### Task 2: Guide Content Split

**Files:**
- Create: `src/omac/guide/roles/planner.md`
- Create: `src/omac/guide/roles/orchestrator.md`
- Create: `src/omac/guide/roles/worker.md`
- Create: `src/omac/guide/roles/reviewer.md`
- Create: `src/omac/guide/roles/acceptor.md`
- Create: `src/omac/guide/artifacts/design.md`
- Create: `src/omac/guide/artifacts/acceptance.md`
- Create: `src/omac/guide/artifacts/manifest.md`
- Create: `src/omac/guide/artifacts/evidence.md`
- Modify: `src/omac/guide/workflow.md`
- Modify: `src/omac/guide/roles.md`

**Interfaces:**
- Consumes: grouped guide loader from Task 1.
- Produces: role-specific protocols and artifact-specific schemas.

- [ ] Add content tests for planner/design and acceptance guide text.
- [ ] Run targeted tests and confirm failures.
- [ ] Split existing guide content into focused files and add missing planner/design/acceptor guidance.
- [ ] Run targeted tests and confirm pass.

### Task 3: Documentation Sync

**Files:**
- Modify: `docs/omac-cli-design.md`
- Modify: `README.md` if it mentions old guide topics.

**Interfaces:**
- Consumes: final grouped topic names.
- Produces: design docs matching current CLI behavior.

- [ ] Add or adjust tests checking guide help output where relevant.
- [ ] Update design doc command tree and knowledge distribution section.
- [ ] Run target tests, then full `python3 -m pytest tests/`.

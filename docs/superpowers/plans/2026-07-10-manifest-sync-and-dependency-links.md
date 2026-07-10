# Manifest Sync And Dependency Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover manifest pushes after remote `main` advances and expose direct DAG predecessor issues in develop issue handoffs.

**Architecture:** Extend the existing `gitsync.commit_manifest` boundary with one guarded fetch/rebase retry. Extend DAG dispatch source reference construction without changing graph metadata or rendering APIs.

**Tech Stack:** Python 3.12, subprocess Git commands, pytest, OMAC `WorkItemStore` and manifest models.

## Global Constraints

- Pipeline code continues to use only `WorkItemStore` and `AgentRuntime` interfaces.
- `blocked_by` remains a list of DAG node keys.
- Automatic Git recovery may rewrite only commits that touch the current manifest path.
- No automatic conflict resolution or force push.
- Existing source issue links and CLI output remain backward compatible.

---

### Task 1: Guarded Manifest Push Recovery

**Files:**
- Modify: `src/omac/core/gitsync.py`
- Test: `tests/test_gitsync.py`

**Interfaces:**
- Consumes: `commit_manifest(path, message, repo_root, engine_type)`
- Produces: the same return type and normal behavior, plus a guarded non-fast-forward recovery path.

- [ ] Add a failing test where another clone advances `main`, the local repo changes only `.omac/m.yaml`, and `commit_manifest` must rebase and push successfully.
- [ ] Add a failing test proving an unrelated local-only commit prevents automatic rebase.
- [ ] Implement upstream discovery, local-only path validation, fetch, rebase, one push retry, and conflict abort.
- [ ] Run `tests/test_gitsync.py` and keep all existing cases green.

### Task 2: Direct DAG Dependency Source References

**Files:**
- Modify: `src/omac/pipeline/loop.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `node.blocked_by`, `manifest.nodes[dep].work_item_id`, existing `normalize_source_refs` and `render_issue_body`.
- Produces: `source_refs` containing inherited source issues followed by direct predecessor develop issues.

- [ ] Add a failing dispatch test with a completed predecessor carrying a work item ID.
- [ ] Assert metadata and body contain the predecessor issue reference and `omac work show` command.
- [ ] Implement a small helper that appends direct dependency refs and skips missing IDs.
- [ ] Run focused loop and dispatch tests.

### Task 3: Regression And Live State Repair

**Files:**
- Verify: `tests/`
- Repair: `/home/ubuntu/code/snake/.omac/贪吃蛇手游.yaml`
- Backfill: Multica issues AITEAM-774 and AITEAM-775 through `WorkItemStore`-backed OMAC commands or engine adapter operations.

**Interfaces:**
- Consumes: updated OMAC CLI, current systemd supervisor, existing manifest commit.
- Produces: remote `main` containing current manifest state and existing Wave 1 issues containing the AITEAM-773 predecessor link.

- [ ] Run the focused tests, then `python3 -m pytest tests/` with the pipx OMAC environment plus user-site pytest.
- [ ] Stop `omac-snake-dag.service` without cancelling Agent runs.
- [ ] Fetch/rebase the manifest-only local commit onto current `origin/main`, push, and restart the service.
- [ ] Backfill both existing Wave 1 issue bodies and source refs without posting comments.
- [ ] Confirm the service is active, both existing Agent runs remain singular, and remote manifest matches local state.


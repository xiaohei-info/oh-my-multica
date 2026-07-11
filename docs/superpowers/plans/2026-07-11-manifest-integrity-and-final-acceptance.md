# Manifest Integrity And Final Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent truncated or stale manifests from falsely converging, persist acceptance documents with plan outputs, and complete the Snake DAG through final acceptance.

**Architecture:** Keep the manifest as the state-machine source of truth, but make every write atomic and serialize mutating DAG commands per manifest. Add narrow runtime invariants for declared closeout nodes and expected acceptance documents. Persist plan outputs together so another machine can resume the same final-acceptance flow.

**Tech Stack:** Python 3.10+, PyYAML, `fcntl`, pytest, Git, Multica, GitHub CLI.

## Global Constraints

- Pipeline and CLI code only use `WorkItemStore` and `AgentRuntime`; no platform CLI shell-outs outside engines.
- Existing manifests without new acceptance metadata remain loadable.
- A declared `meta.closeout_node` must exist before execution can converge.
- Plan output persistence must include both manifest and acceptance files when acceptance is enabled.
- All production changes follow red-green TDD and the full test suite must pass.

---

### Task 1: Atomic Manifest Writes

**Files:**
- Modify: `src/omac/core/manifest.py`
- Modify: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `save_manifest(manifest, path)`.
- Produces: the same public function with atomic same-directory replacement semantics.

- [ ] Add a regression test that injects a dump failure after partial output and proves the previous manifest remains byte-for-byte intact.
- [ ] Run the focused test and confirm it fails with the current direct overwrite.
- [ ] Write to a temporary file, flush and fsync it, then replace the target with `os.replace`; remove the temporary file on failure.
- [ ] Run focused manifest tests.

### Task 2: Runtime Integrity And Writer Lock

**Files:**
- Modify: `src/omac/core/lint.py`
- Modify: `src/omac/cli/commands/dag.py`
- Modify: `tests/test_lint.py`
- Modify: `tests/test_cli_dag.py`

**Interfaces:**
- Consumes: `manifest.meta.closeout_node`, `_loop_or_single(args, single_round)`.
- Produces: closeout validation and a non-blocking per-manifest mutation lock for `dag run` and `dag tick`.

- [ ] Add a lint regression test for a declared but missing closeout node.
- [ ] Add a CLI regression test proving `dag run` rejects a missing closeout node before dispatch.
- [ ] Add a CLI regression test proving a second mutating command for the same manifest exits with a teaching validation error.
- [ ] Implement the smallest closeout invariant and `/tmp` advisory lock keyed by the absolute manifest path.
- [ ] Run focused DAG and lint tests.

### Task 3: Persist Acceptance With Plan Outputs

**Files:**
- Modify: `src/omac/core/gitsync.py`
- Modify: `src/omac/pipeline/plan.py`
- Modify: `src/omac/cli/commands/dag.py`
- Modify: `tests/test_gitsync.py`
- Modify: `tests/test_cli_plan.py`
- Modify: `tests/test_cli_dag.py`

**Interfaces:**
- Produces: `commit_files(paths, message, repo_root='.', engine_type=None)` plus the existing `commit_manifest` wrapper.
- Produces manifest metadata `acceptance_required` and `acceptance_file` for new plan outputs.

- [ ] Add tests proving plan create/resume mark acceptance expectations and sync both files for the Multica engine.
- [ ] Add a DAG test proving a manifest that explicitly requires acceptance fails when the file is absent.
- [ ] Generalize Git sync to stage/commit/push a bounded path set while retaining existing manifest-only safety behavior.
- [ ] Call the sync after plan create/resume writes its artifacts.
- [ ] Make `_maybe_acceptance` reject a missing explicitly required document instead of silently skipping it.
- [ ] Run focused plan, Git sync, and acceptance tests.

### Task 4: Verification And Publication

**Files:**
- Modify as required by failing tests only.

- [ ] Run all focused tests from Tasks 1-3.
- [ ] Run `python3 -m pytest tests/` in an environment containing project and test dependencies.
- [ ] Review the diff for unrelated changes.
- [ ] Commit and push the change to remote `main`.
- [ ] Reinstall/update the local pipx `omac` command from the latest checkout.

### Task 5: Restore And Complete Snake

**Files:**
- Create: `/home/ubuntu/code/snake/.omac/贪吃蛇手游.acceptance.yaml`
- Modify: `/home/ubuntu/code/snake/.omac/贪吃蛇手游.yaml`

**Interfaces:**
- Consumes: accepted Multica deliverables AITEAM-707 and AITEAM-734.
- Produces: the approved 10-node DAG, preserving the completed state of the existing eight nodes.

- [ ] Restore the acceptance document from AITEAM-707.
- [ ] Merge `leaderboard-profile-ui` and `closeout-e2e-docs` from AITEAM-734 into the current manifest; assign configured workers and preserve prior node state.
- [ ] Add acceptance expectation metadata and validate the closeout node.
- [ ] Run `omac dag check --no-review`, commit, and push Snake state files.
- [ ] Run `omac dag run` through Wave 3, Wave 4, and final acceptance.
- [ ] If final acceptance fails, verify incremental decompose and fix-node convergence.

### Task 6: Remaining CLI Recovery Coverage

**Files:**
- No production files unless a test reveals a defect.

- [ ] Exercise `node retry --worker`, `node accept`, and `node abandon` on isolated manifest copies.
- [ ] Exercise `dag tick` exit 10 and exit 20 with the mock engine.
- [ ] Exercise `plan confirm` success on an isolated live smoke issue or document why a new agent run is required.
- [ ] Record the final command matrix: live Snake, isolated CLI, and automated coverage.

# Flexible DAG Scope Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Treat `scope_paths` as a non-exhaustive primary ownership scope, then unblock and resume the snake auth DAG node without weakening its security contract.

**Architecture:** Preserve the existing manifest schema and runtime behavior. Change only the contract semantics exposed through dispatch text and role/artifact guides, backed by focused regression tests; then update the current snake manifest, reuse AITEAM-777 through `omac node retry`, and restart the existing DAG runner.

**Tech Stack:** Python 3.14, pytest, Markdown guides, YAML manifest, pipx editable install, Multica engine.

## Global Constraints

- `non_goals` remains a hard rejection boundary.
- `scope_paths` remains optional structured metadata and is backward compatible.
- Necessary supporting files outside `scope_paths` are allowed only when required by the node contract and explained in PR or verification evidence.
- Shared-contract, coverage, verification, PR-base, reviewer, and security gates remain unchanged.
- The snake auth implementation must use Argon2id or bcrypt with cost at least 12; scrypt is not allowed.

---

### Task 1: Render Scope Paths As Primary Ownership

**Files:**
- Modify: `tests/test_dispatch.py`
- Modify: `src/omac/pipeline/dispatch.py`
- Modify: `src/omac/core/manifest.py`

**Interfaces:**
- Consumes: `Contract.scope_paths: list`
- Produces: `render_issue_body(...)` text describing a non-exhaustive primary scope.

- [ ] **Step 1: Write the failing dispatch regression test**

Replace the existing hard-boundary assertions with checks for these exact semantics:

```python
def test_scope_paths_rendered_as_primary_scope_when_present(self):
    c = Contract(objective="o", acceptance=["a"], scope_paths=["src/auth/**"])
    n = Node(id="n", worker="alice", title="t", contract=c)
    body = render_issue_body(n, c, TaskKind.DEVELOP, "ID")
    assert "主要代码归属范围" in body
    assert "src/auth/**" in body
    assert "必要配套文件" in body
    assert "PR 或 verification" in body
    assert "越界改动即 reject" not in body
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python3 -m pytest tests/test_dispatch.py -k scope_paths -q`

Expected: FAIL because the body still renders the exact allowlist rule.

- [ ] **Step 3: Implement the minimal wording change**

In `render_issue_body`, render `scope_paths` as the expected center of the change, permit contract-required supporting files, and require the worker to explain them. Update the `Contract.scope_paths` comment to match the public semantics.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m pytest tests/test_dispatch.py -k scope_paths -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omac/pipeline/dispatch.py src/omac/core/manifest.py tests/test_dispatch.py
git commit -m "Relax DAG scope paths to primary ownership"
```

### Task 2: Align Orchestrator, Worker, Reviewer, And Manifest Guides

**Files:**
- Modify: `tests/test_guide.py`
- Modify: `tests/test_cli.py`
- Modify: `src/omac/guide/roles/orchestrator.md`
- Modify: `src/omac/guide/roles/worker.md`
- Modify: `src/omac/guide/roles/reviewer.md`
- Modify: `src/omac/guide/artifacts/manifest.md`

**Interfaces:**
- Consumes: the Task 1 primary-scope semantics.
- Produces: one consistent Agent instruction across decomposition, implementation, and review.

- [ ] **Step 1: Add failing guide assertions**

Add assertions that guides contain `主要代码归属范围`, `必要配套文件`, and an explicit statement that `scope_paths` is not an exhaustive file allowlist. Assert the old phrase `越界即 reject` is absent from scope guidance.

- [ ] **Step 2: Run focused guide tests and verify failure**

Run: `python3 -m pytest tests/test_guide.py tests/test_cli.py -q`

Expected: FAIL because current guides do not define flexible scope semantics.

- [ ] **Step 3: Update the guides**

Document these rules:

```text
scope_paths 只列稳定的主要代码归属范围，不穷举依赖清单、锁文件、migration、生成物或构建配置。
完成 contract 所必需的配套文件可以修改，但 worker 必须在 PR 或 verification 中说明原因。
reviewer 只因无关扩张、并行边界破坏或 non_goals 违规而拒绝，不因必要配套文件未被预先列出而拒绝。
```

- [ ] **Step 4: Run focused guide tests**

Run: `python3 -m pytest tests/test_guide.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omac/guide tests/test_guide.py tests/test_cli.py
git commit -m "Guide agents on flexible DAG scope ownership"
```

### Task 3: Verify And Install OMAC

**Files:**
- Verify: all repository tests.
- Install: `/home/ubuntu/.local/share/pipx/venvs/omac`

**Interfaces:**
- Produces: `/home/ubuntu/.local/bin/omac` executing the current checkout.

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/`

Expected: all tests PASS.

- [ ] **Step 2: Confirm no stale wording remains**

Run: `rg -n "代码范围限定在以下路径,越界改动即 reject|scope_paths.*白名单" src tests`

Expected: no stale hard-allowlist guidance.

- [ ] **Step 3: Refresh the pipx editable installation**

Run: `pipx install --force --editable /home/ubuntu/code/oh-my-multica`

Expected: `omac` installed successfully.

- [ ] **Step 4: Verify installed guide output**

Run: `/home/ubuntu/.local/bin/omac guide role worker`

Expected: output includes `主要代码归属范围` and `必要配套文件`.

### Task 4: Unblock And Resume The Snake DAG

**Files:**
- Modify: `/home/ubuntu/code/snake/.omac/贪吃蛇手游.yaml`
- Runtime: AITEAM-777 and `omac-snake-dag.service`

**Interfaces:**
- Consumes: updated installed OMAC and existing `auth-server-api` work item ID.
- Produces: one resumed auth worker run using AITEAM-777.

- [ ] **Step 1: Extend the auth node's primary scope**

Add `package.json` below the existing auth `scope_paths`. Keep the approved password-hashing `non_goals` unchanged.

- [ ] **Step 2: Validate the manifest**

Run: `/home/ubuntu/.local/bin/omac dag check .omac/贪吃蛇手游.yaml`

Expected: validation PASS.

- [ ] **Step 3: Reset the blocked node**

Run: `/home/ubuntu/.local/bin/omac node retry .omac/贪吃蛇手游.yaml auth-server-api`

Expected: node status becomes `todo`, work item ID remains `2d1376b7-0de7-4cb3-b662-5bcb0e1d4a6e`.

- [ ] **Step 4: Start the background DAG runner**

Start `omac-snake-dag.service` with the Multica workspace/project environment and no restart loop.

- [ ] **Step 5: Verify one worker dispatch**

Confirm:

```text
service = active
manifest auth-server-api = in_progress
AITEAM-777 status = in_progress
AITEAM-777 phase = authoring
exactly one new direct worker run = running
updated contract/body describes primary scope and permits necessary supporting files
```

- [ ] **Step 6: Continue low-frequency monitoring**

Inspect Agent logs, PR, verification, reviewer, merge, metadata, and dependent-node dispatches until the DAG converges or reaches a new genuine decision point.

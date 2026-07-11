# Agent-first Content Architecture Implementation Plan

> Status: implemented and verified on 2026-07-11. The unchecked boxes below preserve
> the original execution sequence rather than representing remaining work.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 issue 收敛为 Human-first 任务视图，将 `work` 与 `guide` 收敛为 Agent-first 动态事实和静态协议通道，同时完整保留现有 guide 语义。

**Architecture:** `render_issue_body` 只渲染 Human 摘要和单行 Agent bootstrap；`build_show_output` 根据 `kind × phase` 动态提供实例事实与 `guide_refs`；guide 继续使用现有 Markdown topic，但统一重组为 Agent 执行协议。所有业务状态仍只通过 `WorkItemStore` / `AgentRuntime`。

**Tech Stack:** Python 3.10+、argparse、Markdown、PyYAML、pytest。

## Global Constraints

- 保持现有 guide topic 与命令路径不变。
- 保持退出码契约不变。
- pipeline / CLI 不直接 shell out Multica/GitHub。
- issue Human-first；`work` 和 `guide` Agent-first。
- 不删除现有 guide 的独有语义。
- 先写测试并确认 RED，再写实现。
- 完成前运行 `python3 -m pytest tests/`。

---

### Task 1: Agent-first `work show/submit` contract

**Files:**
- Modify: `src/omac/pipeline/dispatch.py`
- Modify: `src/omac/cli/commands/work.py`
- Modify: `src/omac/cli/output.py`
- Test: `tests/test_cli_work.py`

**Interfaces:**
- Consumes: `TaskKind`, `TaskPhase`, `WorkItem`, existing `submit_template_for`.
- Produces: `guide_refs_for(kind, phase) -> list[str]`; expanded `build_show_output`; JSON-default work commands.

- [ ] **Step 1: Write failing guide-ref and complete-context tests**

```python
def test_plan_authoring_show_is_self_contained_and_points_to_exact_guides():
    item = _make_item(store, TaskKind.PLAN, TaskPhase.AUTHORING)
    item.description = "用户要求设计兼容旧 token 的登录流程"
    out = build_show_output(item, "planner:alice")
    assert out["task"]["title"] == item.title
    assert out["task"]["status"] == item.status.value
    assert out["context"]["issue_description"] == item.description
    assert out["guide_refs"] == [
        "omac guide role planner",
        "omac guide artifact design",
    ]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest tests/test_cli_work.py -k 'self_contained or guide_refs' -q`

Expected: FAIL because `status`, `issue_description`, and `guide_refs` do not exist.

- [ ] **Step 3: Implement the minimal mapping and output fields**

Add one mapping from `(TaskKind, TaskPhase)` to ordered guide commands:

```python
GUIDE_REFS_BY_KIND_PHASE = {
    (TaskKind.PLAN, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact design"],
    (TaskKind.ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role planner", "omac guide artifact acceptance"],
    (TaskKind.DECOMPOSE, TaskPhase.AUTHORING): [
        "omac guide role orchestrator", "omac guide artifact manifest"],
    (TaskKind.DEVELOP, TaskPhase.AUTHORING): [
        "omac guide role worker", "omac guide artifact evidence"],
    (TaskKind.FINAL_ACCEPTANCE, TaskPhase.AUTHORING): [
        "omac guide role acceptor", "omac guide artifact acceptance",
        "omac guide artifact evidence"],
}
```

Add review mappings to `role reviewer` plus the corresponding artifact. Extend output without removing existing keys. Change `add_output_flag` to accept a `default` argument, then register both work subcommands with `default="json"` while retaining `--output table`.

- [ ] **Step 4: Run work tests**

Run: `python3 -m pytest tests/test_cli_work.py -q`

Expected: PASS.

### Task 2: Human-first issue rendering

**Files:**
- Modify: `src/omac/pipeline/dispatch.py`
- Modify: `src/omac/pipeline/tasks.py`
- Test: `tests/test_dispatch.py`
- Test: `tests/test_tasks.py`

**Interfaces:**
- Consumes: `render_source_refs_section`, contract summary fields, engine env.
- Produces: concise Human issue body with a single Agent bootstrap.

- [ ] **Step 1: Write failing issue-body tests**

```python
def test_issue_body_is_human_first_with_one_agent_entry():
    body = render_issue_body(node, contract, TaskKind.DEVELOP, "ISSUE-9")
    assert "Agent 入口" in body
    assert "omac work show ISSUE-9 --output json" in body
    assert "omac guide role worker" not in body
    assert "omac work submit ISSUE-9" not in body
    assert "## 任务摘要" in body
    assert "## 完成标准" in body
    assert "## Agent 硬约束" not in body
```

- [ ] **Step 2: Run issue rendering tests and verify RED**

Run: `python3 -m pytest tests/test_dispatch.py tests/test_tasks.py -q`

Expected: FAIL on the old four-step bootstrap and hard-constraint dump.

- [ ] **Step 3: Rewrite renderer without changing task lifecycle**

Render the exact `work show --output json` command first, then Chinese Human sections:

```python
bootstrap = (
    "> **Agent 入口:** 先读取当前任务的权威 JSON 上下文。\n\n"
    f"```bash\n{env_prefix}omac work show {issue_id} --output json\n```"
)
briefing = "## 任务摘要\n" + "\n".join(briefing_lines)
```

Add `include_commands: bool = True` to `render_source_refs_section`; issue bodies pass `False`, while `work show` keeps the default. Change embedded upstream `<details open>` to collapsed `<details>`.

- [ ] **Step 4: Run issue/task tests**

Run: `python3 -m pytest tests/test_dispatch.py tests/test_tasks.py -q`

Expected: PASS.

### Task 3: Reorganize every guide as an Agent protocol

**Files:**
- Modify: `src/omac/cli/commands/guide.py`
- Modify: `src/omac/guide/workflow.md`
- Modify: `src/omac/guide/roles.md`
- Modify: `src/omac/guide/recovery.md`
- Modify: `src/omac/guide/roles/planner.md`
- Modify: `src/omac/guide/roles/orchestrator.md`
- Modify: `src/omac/guide/roles/worker.md`
- Modify: `src/omac/guide/roles/reviewer.md`
- Modify: `src/omac/guide/roles/acceptor.md`
- Modify: `src/omac/guide/artifacts/design.md`
- Modify: `src/omac/guide/artifacts/acceptance.md`
- Modify: `src/omac/guide/artifacts/manifest.md`
- Modify: `src/omac/guide/artifacts/evidence.md`
- Test: `tests/test_guide.py`

**Interfaces:**
- Consumes: existing topic loader and validators.
- Produces: unchanged command paths with Agent-first content and preserved semantics.

- [ ] **Step 1: Add failing structure and migration-semantic tests**

Require every role guide to contain `适用条件`, `指令优先级`, `完成条件`, `阻塞与升级`, `错误写法`, and `交付`; require every artifact guide to contain `使用场景`, `合法示例`, `校验硬门`, `常见错误`, and `提交`. Retain assertions for all existing unique concepts.

- [ ] **Step 2: Run guide tests and verify RED**

Run: `python3 -m pytest tests/test_guide.py -q`

Expected: FAIL because current guides do not share the Agent protocol skeleton.

- [ ] **Step 3: Reorganize Markdown without deleting unique semantics**

Move behavior to role guides, schema detail to artifact guides, and use these exact skeletons:

```markdown
## 适用条件
## 指令优先级
## 权威输入
## 执行步骤
## 完成条件
## 返工路径
## 阻塞与升级
## 禁止事项
## 错误写法 → 正确写法
## 交付
```

```markdown
## 使用场景
## 最小合法示例
## 字段语义
## 校验硬门
## 常见错误 → 修正
## 提交
```

Keep short cross-references where a critical rule must be visible in both. Preserve every concept listed in the design document's migration matrix.

- [ ] **Step 4: Validate real examples and guide output**

Run: `python3 -m pytest tests/test_guide.py tests/test_evidence.py tests/test_manifest.py tests/test_delivery_acceptance.py -q`

Expected: PASS.

### Task 4: Human/operator help alignment and regression

**Files:**
- Modify: `src/omac/cli/commands/work.py`
- Modify: `src/omac/cli/commands/guide.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`
- Test: `tests/test_guide.py`
- Test: `tests/test_cli_work.py`

**Interfaces:**
- Consumes: completed issue/work/guide content contracts.
- Produces: concise help that clearly marks Agent-only commands and Human/operator surfaces.

- [ ] **Step 1: Write failing help assertions**

Assert `work --help` says Agent-only and JSON-first; `guide --help` says static Agent protocol and tells the caller to start from `work show`; README explains Human issue vs Agent work/guide boundaries.

- [ ] **Step 2: Run help tests and verify RED**

Run: `python3 -m pytest tests/test_cli.py tests/test_guide.py tests/test_cli_work.py -q`

Expected: FAIL on old mixed-audience wording.

- [ ] **Step 3: Update help and README minimally**

Use these audience statements while keeping command trees and exit codes unchanged:

```text
work: 被派发 Agent 的任务读取与结构化交付入口；默认输出 JSON。
guide: Agent 的静态行为协议；任务实例事实始终以 work show 为准。
issue: Human 任务视图，顶部只保留 work show bootstrap。
```

Remove duplicate hard-rule dumps from help when the corresponding guide is authoritative; keep short examples and exact next commands.

- [ ] **Step 4: Run focused and full verification**

Run: `python3 -m pytest tests/test_cli.py tests/test_guide.py tests/test_cli_work.py tests/test_dispatch.py tests/test_tasks.py -q`

Run: `python3 -m pytest tests/`

Expected: all tests pass with no collection errors.

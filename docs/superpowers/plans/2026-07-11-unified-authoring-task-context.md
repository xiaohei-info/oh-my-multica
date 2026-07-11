# Unified Authoring Task Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 final-acceptance、增量 decompose 与普通任务复用同一条 authoring issue 创建路径，并携带完整 OMAC 命令、集成分支、仓库和上游 issue 链。

**Architecture:** 在 `pipeline/tasks.py` 提取只负责创建 authoring issue 的小原语，生命周期等待仍分别留在 `run_task` 和 `acceptance.py`。`acceptance.py` 从 Manifest 和 ProjectInfo 构造结构化 contract/source refs，`dispatch.render_issue_body` 同时支持 dataclass 与 dict contract。

**Tech Stack:** Python 3、dataclasses、PyYAML、pytest、现有 WorkItemStore/AgentRuntime 抽象。

## Global Constraints

- pipeline/cli 只能调用 `WorkItemStore` / `AgentRuntime`，不得 shell out Multica、Git 或 GitHub CLI。
- metadata 只保存稳定结构化字段和 `*_ref`，验收文档与 Manifest 进入 contract 附件。
- final acceptance 外层循环、退出码和增量合并语义保持不变。
- 新行为严格 TDD；完成前运行 `python3 -m pytest tests/`。
- 术语继续使用“结果回收 / 进行中节点 / 就绪节点”。

---

### Task 1: 统一 Authoring Issue 创建原语

**Files:**
- Modify: `src/omac/pipeline/tasks.py`
- Modify: `src/omac/pipeline/dispatch.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Produces: `AuthoringTaskSpec`
- Produces: `create_authoring_task(engine, spec) -> WorkItem`
- Produces: dict-compatible `_contract_summary(contract, key, fallback)`
- Consumes: existing `render_issue_body`, `normalize_source_refs`, `WorkItemStore`

- [ ] **Step 1: Write failing tests for the shared creation primitive**

Add to `tests/test_tasks.py`:

```python
def test_create_authoring_task_renders_body_contract_and_source_refs():
    eng = _engine()
    project = eng.store.create_project(
        "ws", "demo", repo_urls=["git@github.com:owner/demo.git"])
    eng.store.config.project_id = project.id
    spec = AuthoringTaskSpec(
        kind=TaskKind.FINAL_ACCEPTANCE,
        title="最终验收 · Demo · 第 1 轮",
        dag_key="final-acceptance-p-demo-r1",
        assignee="alice",
        description="按 ACC-001 逐项走查。",
        contract={
            "acceptance_doc": {"flows": []},
            "flows": ["ACC-001"],
            "pr_base": "main",
            "repo_urls": ["git@github.com:owner/demo.git"],
        },
        source_refs=[{"label": "最终开发交付", "issue_id": "closeout-1"}],
    )

    item = create_authoring_task(eng, spec)

    assert "OMAC_ENGINE=mock OMAC_WORKSPACE_ID=ws" in item.description
    assert f"omac work show {item.id}" in item.description
    assert "pr_base=main" in item.description
    assert "git@github.com:owner/demo.git" in item.description
    assert item.contract["acceptance_doc"] == {"flows": []}
    assert item.source_refs == [
        {"label": "最终开发交付", "issue_id": "closeout-1"}
    ]
```

Add a second test proving `run_task` still uses the same helper by monkeypatching
`create_authoring_task` and asserting one call.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_tasks.py::test_create_authoring_task_renders_body_contract_and_source_refs -q
```

Expected: FAIL because `AuthoringTaskSpec` / `create_authoring_task` do not exist.

- [ ] **Step 3: Write failing dict-contract rendering tests**

Add to `tests/test_dispatch.py`:

```python
def test_final_acceptance_body_reads_mapping_contract_and_repositories():
    node = SimpleNamespace(
        id="fa", title="最终验收 · Demo · 第 1 轮",
        description="按验收文档逐项走查。", reviewer=None,
    )
    contract = {
        "acceptance_doc": {"flows": []},
        "acceptance": ["ACC-001"],
        "pr_base": "main",
        "repo_urls": ["git@github.com:owner/demo.git"],
    }

    body = render_issue_body(
        node, contract, TaskKind.FINAL_ACCEPTANCE, "fa-1",
        engine_env={
            "OMAC_ENGINE": "multica",
            "OMAC_WORKSPACE_ID": "ws-1",
            "OMAC_PROJECT_ID": "project-1",
        },
    )

    assert "- acceptance:\n  - ACC-001" in body
    assert "pr_base=main" in body
    assert "## 目标仓库" in body
    assert "git@github.com:owner/demo.git" in body
```

- [ ] **Step 4: Run dict-contract test and verify RED**

Run:

```bash
python3 -m pytest tests/test_dispatch.py::test_final_acceptance_body_reads_mapping_contract_and_repositories -q
```

Expected: FAIL because `_contract_summary` only uses attributes and no repository
section is rendered.

- [ ] **Step 5: Implement the minimal shared primitive**

In `src/omac/pipeline/tasks.py`, add:

```python
from dataclasses import dataclass, field


@dataclass
class AuthoringTaskSpec:
    kind: TaskKind
    title: str
    dag_key: str
    assignee: str
    description: str = ""
    contract: Any = None
    source_refs: List[dict] = field(default_factory=list)
    source_of_truth: Dict[str, str] = field(default_factory=dict)


def create_authoring_task(engine, spec: AuthoringTaskSpec) -> WorkItem:
    store = engine.store
    item = store.create_work_item(
        workspace_id=store.config.workspace_id,
        title=spec.title,
        description=spec.title,
        dag_key=spec.dag_key,
        worker=spec.assignee,
        kind=spec.kind,
    )
    body_node = SimpleNamespace(
        title=spec.title,
        description=spec.description,
        reviewer=None,
        id=item.id,
    )
    env = _engine_env(engine)
    refs = normalize_source_refs(spec.source_refs, engine_env=env)
    body = render_issue_body(
        body_node, spec.contract, spec.kind, item.id,
        source_refs=refs, engine_env=env,
        issue_key=getattr(item, "identifier", None),
    )
    if spec.source_of_truth:
        body += "\n\n" + _render_source_of_truth(spec.source_of_truth)
    if spec.contract is not None:
        store.set_node_contract(item.id, spec.contract)
    return store.update_work_item_metadata(
        item.id, description=body, source_refs=refs)
```

Replace the new-item branch in `run_task` with this helper. Preserve resume behavior.

- [ ] **Step 6: Implement dict contract and repository rendering**

In `src/omac/pipeline/dispatch.py`:

```python
from collections.abc import Mapping


def _contract_summary(contract, key, fallback):
    if contract is None:
        return fallback
    value = contract.get(key) if isinstance(contract, Mapping) \
        else getattr(contract, key, None)
    if isinstance(value, list):
        return value if value else fallback
    return value if value not in (None, "") else fallback
```

Render repositories before the hard constraints:

```python
repo_urls = _contract_summary(contract, "repo_urls", None)
repositories = ""
if repo_urls:
    repositories = "## 目标仓库\n" + "\n".join(
        f"- `{url}`" for url in repo_urls)
```

Use mapping-safe coverage lookup:

```python
coverage_gate = _contract_summary(contract, "coverage_gate", None)
```

- [ ] **Step 7: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_tasks.py tests/test_dispatch.py -q
```

Expected: both files pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/omac/pipeline/tasks.py src/omac/pipeline/dispatch.py \
  tests/test_tasks.py tests/test_dispatch.py
git commit -m "refactor: unify authoring issue creation"
```

---

### Task 2: Final Acceptance 与增量 Decompose 接入完整上下文

**Files:**
- Modify: `src/omac/pipeline/acceptance.py`
- Test: `tests/test_delivery_acceptance.py`

**Interfaces:**
- Produces: `_resolve_operation_branch(manifest) -> str`
- Produces: `_project_repo_urls(store) -> list[str]`
- Produces: `_acceptance_source_refs(manifest, engine_env) -> list[dict]`
- Consumes: `AuthoringTaskSpec`, `create_authoring_task`

- [ ] **Step 1: Write failing operation-branch tests**

Add to `tests/test_delivery_acceptance.py`:

```python
def test_resolve_operation_branch_from_node_contracts():
    manifest = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", contract=Contract(pr_base="main")),
        "b": Node(id="b", worker="bob", contract=Contract(pr_base="main")),
    })
    assert _resolve_operation_branch(manifest) == "main"


def test_resolve_operation_branch_rejects_missing_or_conflicting_values():
    missing = Manifest(meta={}, nodes={"a": Node(id="a", worker="alice")})
    with pytest.raises(NeedsDecision, match="pr_base"):
        _resolve_operation_branch(missing)

    conflicting = Manifest(meta={}, nodes={
        "a": Node(id="a", worker="alice", contract=Contract(pr_base="main")),
        "b": Node(id="b", worker="bob", contract=Contract(pr_base="release")),
    })
    with pytest.raises(NeedsDecision, match="多个 pr_base"):
        _resolve_operation_branch(conflicting)
```

- [ ] **Step 2: Run operation-branch tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_delivery_acceptance.py -k operation_branch -q
```

Expected: FAIL because `_resolve_operation_branch` does not exist.

- [ ] **Step 3: Write failing final-acceptance context test**

Create a mock project and done manifest containing `source_issues`, `closeout_node`,
and a closeout work item. Configure one successful acceptance result, run the loop,
then assert:

```python
item = next(
    item for item in engine.store.list_work_items("ws")
    if item.kind == TaskKind.FINAL_ACCEPTANCE
)
assert "OMAC_ENGINE=mock OMAC_WORKSPACE_ID=ws OMAC_PROJECT_ID=project-1" \
    in item.description
assert "pr_base=main" in item.description
assert "git@github.com:owner/demo.git" in item.description
assert "最终开发交付" in item.description
assert item.contract["acceptance_doc"]["flows"][0]["id"] == "ACC-001"
assert item.contract["repo_urls"] == ["git@github.com:owner/demo.git"]
assert item.source_refs[-1]["issue_id"] == "closeout-issue"
```

Also assert that neither `item.description` nor mock metadata contains the full raw
`acceptance_doc:` YAML block.

- [ ] **Step 4: Write failing incremental decompose context test**

Drive first-round failure and assert the generated decompose issue:

```python
assert item.contract["mode"] == "incremental"
assert item.contract["failed_items"] == ["ACC-001"]
assert "manifest" in item.contract
assert item.source_refs[-1]["label"] == "触发验收 · 第 1 轮"
assert "ACC-001" in item.description
assert "当前 Manifest" not in item.description
```

- [ ] **Step 5: Run context tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_delivery_acceptance.py -k "context or operation_branch" -q
```

Expected: FAIL because acceptance tasks still use raw `yaml.dump(payload)` bodies.

- [ ] **Step 6: Implement branch, repository and source-ref derivation**

In `src/omac/pipeline/acceptance.py`:

```python
def _resolve_operation_branch(manifest: Manifest) -> str:
    explicit = str(manifest.meta.get("pr_base") or "").strip()
    if explicit:
        return explicit
    values = {
        node.contract.pr_base
        for node in manifest.nodes.values()
        if node.contract is not None and node.contract.pr_base
    }
    if len(values) == 1:
        return next(iter(values))
    if not values:
        raise NeedsDecision(
            "最终验收缺少 pr_base —— 请修复 Manifest contract.pr_base 后重跑 dag run")
    raise NeedsDecision(
        f"最终验收发现多个 pr_base: {sorted(values)} —— 请统一后重跑 dag run")


def _project_repo_urls(store) -> List[str]:
    project_id = store.config.project_id
    if not project_id:
        return []
    for project in store.list_projects(store.config.workspace_id):
        if project.id == project_id:
            return list(project.repos)
    return []
```

Build source refs from `manifest.meta.source_issues`, then append the closeout node's
`work_item_id`. Use labels `设计方案`、`验收文档`、`任务拆解`、`最终开发交付`.

- [ ] **Step 7: Replace raw payload dispatch with AuthoringTaskSpec**

Change `_dispatch_and_wait` to accept `spec: AuthoringTaskSpec`:

```python
item = create_authoring_task(engine, spec)
item_id = item.id
store.mark_in_progress(item_id)
store.assign_work_item(item_id, spec.assignee, "worker")
runtime.wake(item_id, spec.assignee, "worker")
```

For final acceptance, use:

```python
contract = {
    "acceptance_doc": _acceptance_doc_raw(acceptance_doc),
    "flows": acceptance_doc.flow_ids,
    "acceptance": acceptance_doc.flow_ids,
    "pr_base": operation_branch,
    "repo_urls": repo_urls,
}
```

Description must be short and human-readable. When `repo_urls` is empty append:

```text
当前 Project 未登记仓库；先运行 `omac init --check` 修复项目资源，再按上游 issue 定位代码。
```

For incremental decompose, put the full current Manifest in contract and use a body
description that lists only failed flow IDs. Append the final-acceptance issue to
source refs before dispatch.

- [ ] **Step 8: Run acceptance tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_delivery_acceptance.py -q
```

Expected: all acceptance-loop, incremental merge and real submit tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/omac/pipeline/acceptance.py tests/test_delivery_acceptance.py
git commit -m "fix: provide complete final acceptance context"
```

---

### Task 3: 修正 Reviewer Evidence Guide 并做完整回归

**Files:**
- Modify: `src/omac/guide/artifacts/evidence.md`
- Modify: `tests/test_guide.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `validate_review_evidence`
- Produces: guide 中可被 validator 接受的 reviewer report 示例

- [ ] **Step 1: Write failing guide-schema test**

In `tests/test_guide.py`, parse the reviewer YAML block from
`load_artifact_topic("evidence")` and validate it against a Contract containing one
integration gate. The essential assertion is:

```python
assert validate_review_evidence(contract, "pass", report) == []
```

Use the existing `Contract` and validator rather than checking strings only.

- [ ] **Step 2: Run guide test and verify RED**

Run:

```bash
python3 -m pytest tests/test_guide.py -k reviewer_example -q
```

Expected: FAIL because the guide contains `integration_gate_mapping: []`.

- [ ] **Step 3: Replace the incomplete reviewer example**

Update `src/omac/guide/artifacts/evidence.md`:

```yaml
review_goals:
  - acceptance 全覆盖且逐条可验证
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
acceptance_mapping:
  - { acceptance: "flow-login", evidence: "tests/e2e/test_login.py", status: pass }
integration_gate_mapping:
  - gate: auth-e2e
    status: pass
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: 登录主链路可用
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
blockers: []
nits: []
```

- [ ] **Step 4: Run guide and evidence tests**

Run:

```bash
python3 -m pytest tests/test_guide.py tests/test_evidence.py -q
```

Expected: all pass.

- [ ] **Step 5: Run all focused regression tests**

Run:

```bash
python3 -m pytest \
  tests/test_tasks.py \
  tests/test_dispatch.py \
  tests/test_delivery_acceptance.py \
  tests/test_cli_work.py \
  tests/test_guide.py \
  tests/test_evidence.py -q
```

Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run:

```bash
python3 -m pytest tests/
```

Expected: zero failures.

- [ ] **Step 7: Verify CLI package and repository state**

Run:

```bash
omac --version
python3 -c 'import omac; print(omac.__file__)'
git diff --check
git status --short
```

Expected: `omac` loads from this checkout/pipx editable installation, diff check is
clean, and only intended files are modified.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/omac/guide/artifacts/evidence.md tests/test_guide.py
git commit -m "docs: make reviewer evidence example executable"
```

- [ ] **Step 9: Push main**

```bash
git push origin main
```

Expected: remote `main` advances to the final local commit.

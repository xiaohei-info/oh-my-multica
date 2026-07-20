# Strict Agent Quality Gates Design

## 1. Decision

oh-my-multica will replace its current development and review evidence contracts
with one strict contract. There is no legacy compatibility mode and no optional
quality profile.

The new contract must prevent these delivery failures:

1. Tests exist only to satisfy a coverage or command gate and do not verify a
   real business behavior.
2. A Worker submits a skeleton, placeholder, temporary implementation, or a
   partial implementation while claiming the node is complete.
3. Production behavior hides a real dependency or data error by returning fake,
   synthetic, mock, or fabricated success data.
4. A Reviewer reports one issue per round instead of returning one complete
   finding batch for the reviewed revision.

The existing `pass-with-nits` control flow remains unchanged:

- Reviewer submits `pass-with-nits`.
- The item returns to the Worker once.
- The Worker applies the requested follow-up and resubmits evidence.
- The item continues to merge or completion without a second Reviewer round.

The follow-up remains on the same canonical pull request. It must create a new
head revision; replacing the reviewed PR with another repository or PR number
is invalid.

Because this path deliberately skips re-review, `pass-with-nits` may contain
only low-risk, precisely scoped follow-up items. Any functional, correctness,
contract, security, data-integrity, compatibility, or verification problem must
use `reject`.

## 2. Core Data Model

The system will use one traceability chain:

```text
required outcome
    -> implementation locations
    -> business test
    -> red/green proof
    -> reviewer coverage
    -> reviewer findings
    -> verdict
```

The stable identifier is `outcome.id`. Every executable requirement assigned to
a node must be represented by exactly one required outcome. Outcomes may point
to an acceptance flow/action or a design source reference, but prose alone is
not sufficient for completion.

Authored manifests contain declarations only. `status`, `work_item_id`,
`merged`, and `merged_at` are runtime-owned fields and are rejected when present
in authoring input, including apparently harmless default values. A resumed
`done` node is trusted only when its platform work item and completed delivery
evidence agree with the node identity.

`dag run` enforces the same complete contract rules as `dag check`; skipping a
separate check command cannot admit a partial contract. The engine resolved
from CLI/environment/config precedence is overlaid onto the invocation config,
so adapters and delivery commands cannot disagree about the active engine.

### 2.1 Acceptance action identifiers

Every acceptance action must have a stable non-empty `id`, unique within its
flow. The stable fully qualified identifier is `<flow-id>.<action-id>`.

Example:

```yaml
flows:
  - id: flow-login
    name: 用户使用有效凭证登录
    actions:
      - id: submit-valid-credentials
        step: 提交有效凭证
        how: 输入账号密码并提交
        expected: 进入 dashboard 并展示当前用户
```

### 2.2 Contract quality section

Every node contract must contain a non-empty `quality` section:

```yaml
quality:
  required_outcomes:
    - id: login-valid-credentials
      source_ref: acceptance#flow-login.submit-valid-credentials

  business_tests:
    - id: test-login-valid-credentials
      outcome_refs:
        - login-valid-credentials
      command: python3 -m pytest tests/e2e/test_login.py
      level: e2e
      real_dependencies:
        - postgres
      must_fail_on_base: true

  runtime_data_policy: real-or-error
```

Rules:

- `required_outcomes` and `business_tests` are non-empty.
- Outcome IDs and business-test IDs are unique inside the node.
- Every outcome is covered by at least one business test.
- Every business test references only declared outcomes.
- `level` is `integration` or `e2e`; unit tests cannot be the only business
  evidence.
- `command` must be present in either `verification_commands` or an integration
  gate command.
- `real_dependencies` is a non-empty list. `none` may be used only when the
  tested behavior genuinely has no external runtime boundary.
- `must_fail_on_base` is required and must be boolean.
- `runtime_data_policy` has the single legal value `real-or-error`.

`real-or-error` means production/runtime code must return real results or expose
the real failure through the repository's established error contract. It must
not manufacture success records, placeholder entities, default balances,
synthetic API responses, or other fabricated business data.

Test fixtures and test doubles remain allowed inside tests. They cannot be the
sole evidence for an integration or end-to-end business test.

## 3. Worker Evidence Contract

Worker verification adds a mandatory `quality` section:

```yaml
quality:
  delivered_revision: def456
  outcome_mapping:
    - outcome: login-valid-credentials
      implementation:
        - src/auth/login.py
      tests:
        - tests/e2e/test_login.py

  regression_proof:
    - test_id: test-login-valid-credentials
      base_ref: abc123
      base_exit_code: 1
      head_ref: def456
      head_exit_code: 0

  runtime_fallbacks: []
  known_gaps: []
  evidence_origin: real
```

Validation rules:

- `delivered_revision` is required and equals the current PR head read through
  the engine adapter at submission time.
- `outcome_mapping` covers every required outcome exactly once.
- Every mapping has at least one implementation path and one test path.
- `regression_proof` covers every business test exactly once.
- `head_exit_code` is zero.
- If `must_fail_on_base` is true, `base_exit_code` must be non-zero.
- `base_ref` and `head_ref` are non-empty and must differ; every `head_ref`
  equals `delivered_revision`.
- `runtime_fallbacks` must be an empty list.
- `known_gaps` must be an empty list.
- `evidence_origin` must be `real`; simulated/mock evidence is rejected.
- Rework submits the same platform-canonical PR identity as the previous
  delivery; a URL alias may normalize, but another repository or PR number is
  rejected.
- Worker integration-gate evidence contains every declared gate exactly once;
  duplicate, unknown, and malformed gate entries are rejected before lookup.
- `artifacts.pr_url` is the only PR identity field. Production delivery accepts
  only `https://github.com/<owner>/<repo>/pull/<number>`; `artifacts.pr` is invalid.

These checks prove traceability and red/green behavior. They do not treat a
Worker's statement as authoritative proof. Reviewer reproduction remains the
semantic gate.

## 4. Reviewer Report Contract

Reviewer reports replace free-form blocker strings with one structured finding
batch tied to a specific revision:

```yaml
reviewed_revision: def456

review_scope:
  changed_files:
    - src/auth/login.py
    - tests/e2e/test_login.py
  all_changed_files_reviewed: true
  all_outcomes_reviewed: true
  all_business_tests_rerun: true
  runtime_fallback_audit_completed: true

findings:
  - id: REV-001
    severity: blocker
    category: runtime-fallback
    location: src/auth/login.py:84
    evidence: 超时分支返回了伪造用户对象
    impact: 隐藏认证依赖故障并产生虚假成功状态
    required_fix: 返回现有 upstream timeout 错误

acceptance_mapping: []
integration_gate_mapping: []
outcome_mapping: []
blockers: []
nits: []
```

The existing `blockers` and `nits` fields remain part of the serialized report
shape only because existing loop behavior consumes their verdict relationship.
They are no longer free-form lists:

- `blockers` contains finding IDs whose severity is `blocker`.
- `nits` contains finding IDs whose severity is `nit`.

This is a schema replacement, not backward compatibility: old string-only
reports are invalid.

### 4.1 Finding requirements

Every finding has:

- a unique stable `id`;
- `severity`: `blocker` or `nit`;
- `category`;
- a precise file/line or artifact location;
- observed evidence;
- concrete impact;
- a copyable or unambiguous required fix.

### 4.2 Completeness requirements

- `reviewed_revision` is required and equals both Worker `delivered_revision`
  and the current PR head.
- `changed_files` is non-empty for develop reviews.
- Every review-scope boolean must be true.
- `outcome_mapping` covers every contract outcome and records `pass` or `fail`.
- Acceptance, outcome, and integration-gate mappings contain every expected key
  exactly once. Duplicate keys, unknown keys, malformed keys, invalid statuses,
  and missing keys are rejected rather than collapsed by dictionary conversion.
- Reviewer independently reproduces every business-test red/green proof.
- Reviewer checks changed production paths for fake/synthetic runtime fallback.
- A report is one complete finding batch for `reviewed_revision`; the Reviewer
  must finish the full diff and risk sweep before submitting it.

The system cannot mathematically prove that a Reviewer found every possible
defect. It can reject partial scope declarations, incomplete outcome coverage,
missing business-test reproduction, and malformed finding batches.

### 4.3 Verdict rules

- `pass`: no findings, no blockers, no nits, every mapping passes.
- `pass-with-nits`: one or more `nit` findings, no blocker findings, every
  required outcome and business test passes.
- `reject`: at least one blocker finding. The report should also include all
  nits found during the same full review sweep.

A Reviewer must use `reject`, not `pass-with-nits`, when the requested change
needs another independent correctness judgment after repair.

## 5. Rework Behavior

### 5.1 Reject

The existing reject loop remains bounded by `retry.review`:

1. Reviewer submits the complete finding batch.
2. The same item returns to the Worker.
3. `work show` exposes the entire previous report.
4. Worker fixes every blocker and addresses every nit, then resubmits the same
   PR with fresh verification evidence.
5. The item enters Reviewer again.
6. Reviewer reruns the complete contract and verifies the previous findings.

### 5.2 Pass with nits

The existing no-second-review flow remains unchanged:

1. Reviewer submits `pass-with-nits` with nit findings only.
2. Worker performs the narrowly defined follow-up once.
3. Worker submits fresh complete verification evidence.
4. OMAC continues directly to merge/completion.

Because no Reviewer checks this follow-up, nits must not alter business logic,
error semantics, data contracts, permissions, persistence, concurrency,
integration behavior, migrations, or public interfaces.

The follow-up uses the same canonical PR and a new head revision. If the
original PR cannot continue, the item is blocked for an explicit decision; a
replacement PR cannot inherit the previous review.

### 5.3 Revision-locked merge

Automatic merge requires both `{pr_url}` and `{delivered_revision}` in the merge
command template. The default GitHub command passes `--match-head-commit` with
the current Worker revision accepted by the evidence gate. For `pass`, that
revision equals the Reviewer revision; for `pass-with-nits`, it is the fresh
follow-up revision. A later head change fails merge and returns through the
bounded merge-rework path.

Merge configuration is validated before node-state mutation. Missing
`{pr_url}` or `{delivered_revision}` is validation failure (exit 5), not a
caller-decision state. GitHub authentication failures use exit 3; platform,
network, CLI availability, timeout, and malformed platform responses use exit 2.

## 6. Mock and Synthetic Data Boundary

The `mock` engine remains available for tests of OMAC's orchestration, adapter,
and state-machine behavior. Its generated verification and review reports must
identify their origin as `mock` and therefore cannot satisfy the new production
evidence contract.

Repository tests may construct fake platform processes, clocks, or dependency
doubles when testing deterministic infrastructure behavior. Business-delivery
tests must still include the real application path declared by the contract.

No global keyword ban on `mock`, `fake`, `stub`, or `placeholder` will be added.
Such a scan would reject legitimate tests and UI text while remaining easy to
evade. The hard gate is structured evidence plus independent review of changed
production paths.

## 7. Error Behavior

All schema failures are `ValidationError` and retain exit code 5. Errors must:

- name the missing or invalid field;
- identify the outcome, test, finding, or mapping involved;
- state how to correct the evidence;
- preserve atomic submit behavior: no metadata or status mutation after a
  failed validation.

Missing an independent Reviewer is a caller decision and returns exit code 20.
The planner/reviewer selection path must no longer fall back to producer
self-review.

## 8. Implementation Scope

Required production changes:

- `core/acceptance.py`: stable action IDs and qualified outcome references.
- `core/manifest.py`: contract quality model and serialization.
- `core/lint.py`: strict quality validation.
- `core/evidence.py`: Worker quality and Reviewer finding validators.
- `pipeline/dispatch.py`: strict submit validation and review context.
- `pipeline/loop.py`: reject preloaded runtime completion without authoritative
  work-item and delivery facts.
- `pipeline/delivery.py` and `core/config.py`: revision-locked merge templates.
- `pipeline/tasks.py`: prohibit self-review fallback.
- engine model/adapters: preserve and expose the new evidence fields.
- mock engine: mark generated evidence as mock and generate schema-valid shapes
  only for tests that explicitly exercise mock behavior.
- Worker, Reviewer, Orchestrator, Planner guides and English equivalents.
- artifact guides and public documentation where the evidence contract is
  described.

The `pass-with-nits` branches in `pipeline/tasks.py` and `pipeline/loop.py` must
not be changed except where necessary to consume the new finding schema.

## 9. TDD and Verification Plan

Tests are written and observed failing before implementation.

### Contract and acceptance tests

- acceptance action without ID is rejected;
- duplicate action IDs are rejected;
- contract without quality is rejected;
- uncovered outcomes and unknown outcome references are rejected;
- unit-only business tests are rejected;
- business-test commands not declared in verification/integration gates are
  rejected.

### Worker evidence tests

- missing outcome mapping is rejected;
- empty implementation/test locations are rejected;
- base and head refs must differ;
- required base failure must be non-zero;
- head failure is rejected;
- runtime fallbacks and known gaps are rejected;
- mock/simulated evidence origin is rejected.
- blank or malformed PR URLs are rejected before an adapter call;
- rework cannot replace the previously delivered canonical PR.

### Reviewer evidence tests

- incomplete review scope is rejected;
- duplicate or malformed finding IDs are rejected;
- free-form blocker/nit strings are rejected;
- findings and blocker/nit ID lists must agree;
- outcome coverage must be complete;
- duplicate, unknown, malformed, invalid-status, and missing mapping entries are
  all rejected;
- develop review requires Worker `delivered_revision` even when the current PR
  head is known;
- `pass`, `pass-with-nits`, and `reject` enforce their exact finding rules;
- one complete reject batch is visible to the Worker on rework.

### State-machine tests

- producer-only Reviewer pool returns exit 20;
- reject still returns to Worker and then Reviewer;
- `pass-with-nits` still returns to Worker once and skips second review;
- strict evidence failures do not mutate metadata or status;
- preloaded `done`, `work_item_id`, `merged`, or `merged_at` authoring state
  cannot bypass dispatch, review, or merge;
- merge commands without both safety placeholders are blocked, and the default
  command is locked to the reviewed head;
- mock orchestration tests use explicit schema-valid mock evidence without
  weakening production validation.

### Completion commands

```bash
python3 -m pytest tests/
git diff --check
```

Completion requires both commands to succeed and all changed documentation to
match the implemented schema.

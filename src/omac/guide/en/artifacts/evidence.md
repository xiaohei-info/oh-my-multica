# Evidence artifact contract

## When to use it

This contract covers three structured evidence shapes: worker verification,
reviewer report, and final acceptance results. Left-shift and authority gates
share them; missing fields fail immediately in `omac work submit`. An issue
submits only the shape named by `work show`.

First run:

```bash
omac work show <issue-id> --output json
```

Its task, context, contract, authority, guide references, and submit command
are current facts. This guide cannot override those facts, the review target, or
the exact submission command.

## Minimum valid examples

The three forms do not replace one another. Fill values from the current contract.

### Worker verification

```yaml
commands:
  - { cmd: "python3 -m pytest tests/auth", exit_code: 0, summary: "passed" }
integration_gates:
  - name: auth-e2e
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: The sign-in critical path works
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
pr_base: feature/login
coverage: 92
env_setup:
  - "docker compose up -d db"
quality:
  delivered_revision: def456
  outcome_mapping:
    - outcome: login-succeeds
      implementation: [src/auth/login.py]
      tests: [tests/e2e/test_login.py]
  regression_proof:
    - test_id: login-e2e
      base_ref: abc123
      base_exit_code: 1
      head_ref: def456
      head_exit_code: 0
  runtime_fallbacks: []
  known_gaps: []
  evidence_origin: real
```

### Reviewer report

```yaml
reviewed_revision: def456
review_goals:
  - Acceptance is fully covered and each item is verifiable
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
review_scope:
  changed_files: [src/auth/login.py, tests/e2e/test_login.py]
  all_changed_files_reviewed: true
  all_outcomes_reviewed: true
  all_business_tests_rerun: true
  runtime_fallback_audit_completed: true
findings: []
outcome_mapping:
  - { outcome: "login-succeeds", status: pass }
acceptance_mapping:
  - { acceptance: "flow-login", evidence: "tests/e2e/test_login.py", status: pass }
integration_gate_mapping:
  - gate: auth-e2e
    status: pass
    source_of_truth: [docs/design.md#auth-flow]
    delivery_goal: The sign-in critical path works
    commands:
      - { cmd: "python3 -m pytest tests/e2e/test_login.py", exit_code: 0 }
    metrics: {}
    artifacts: []
blockers: []
nits: []
```

### Final acceptance results

```yaml
- id: flow-login
  status: pass
- id: flow-payment
  status: fail
  notes: The payment success page does not show the order number
```

## Field semantics

### Worker verification

| Field | Meaning |
|---|---|
| `commands` | Actual results for contract `verification_commands`; command text matches exactly and exit code is 0. |
| `integration_gates` | Command, metric, artifact, source, and delivery-goal evidence by gate name. |
| `pr_base` | Exactly matches contract `pr_base`. |
| `coverage` | Numeric coverage meeting `coverage_gate`. |
| `env_setup` | Reproducible preparation steps; non-empty strings when the contract has integration gates. |
| `quality.delivered_revision` | Exact PR head revision delivered by the Worker; it must match the platform's current PR head at submission. |
| `quality.outcome_mapping` | Real implementation and business-test files for every required outcome. |
| `quality.regression_proof` | Base/head revisions and exit codes for each business test; the base fails when required and head passes. |
| `quality.runtime_fallbacks` / `known_gaps` | Both empty; fake/mock/synthetic runtime fallbacks or incomplete requirements cannot be submitted as complete. |
| `quality.evidence_origin` | `real` for production delivery; mock-engine evidence is only for OMAC orchestration tests. |

Submit the PR URL separately through `--pr-url`, not in verification YAML.

### Reviewer report

| Field | Meaning |
|---|---|
| `review_goals` | Non-empty list of independently verified review goals. |
| `reviewed_revision` | Exact revision covered by this complete review; a new revision requires a new complete review. |
| `diff_reviewed` / `tests_rerun` / `coverage_checked` | `true`: the diff was read, tests rerun independently, and coverage checked. |
| `integration_tests_rerun` | `true` when the contract has integration gates. |
| `review_scope` | Changed files plus true flags for all files, outcomes, business tests, and runtime-fallback audit. |
| `findings` | Complete finding batch for the revision; each has id, severity, category, location, evidence, impact, and required_fix. |
| `outcome_mapping` | Every required outcome; all pass for approving verdicts. |
| `acceptance_mapping` | Evidence and pass/fail for every contract acceptance flow. |
| `integration_gate_mapping` | Independently reproduced gate results aligned with the contract. |
| `blockers` | Exact IDs of blocker findings. |
| `nits` | Exact IDs of nit findings. |

Submit verdict through `--verdict`, not report YAML. Valid values are `pass`,
`pass-with-nits`, and `reject`.

### Final acceptance results

| Field | Meaning |
|---|---|
| `id` | Acceptance-document flow ID. |
| `status` | Only `pass` or `fail`. |
| `notes` | Required for `fail`, with reproducible observed failure; optional for pass. |

## Validation gates

### Worker verification

1. Submit a canonical GitHub PR URL (`https://github.com/<owner>/<repo>/pull/<number>`) and verification file; the PR is deliverable and not draft. `artifacts.pr_url` is the only PR field and `artifacts.pr` is forbidden. Rework keeps the same canonical PR.
2. Commands cover the contract's exact commands and exit 0.
3. Gate sources and delivery goals exactly match the contract.
4. Metrics meet thresholds and required artifacts are present.
5. With integration gates, `env_setup` is a non-empty list of non-empty strings.
6. PR base matches and coverage is numeric and at least the gate.
7. `quality.delivered_revision` matches the current PR head, and every regression
   proof `head_ref` matches that revision.
8. Outcome mappings, regression proofs, and integration-gate evidence are
   complete with every ID/name exactly once; duplicate, unknown, or malformed
   gates are rejected. Runtime fallbacks and known gaps are empty; evidence
   origin is `real`.

### Reviewer report

1. `reviewed_revision`, `review_goals`, and Worker `quality.delivered_revision`
   are present; `reviewed_revision` matches both the Worker revision and current PR head;
   review scope lists changed files and all four completeness flags are true.
2. The reviewer completes all changed files, outcomes, real business tests, and
   fake/runtime-fallback audit in one sweep and submits one complete finding batch.
3. Findings are complete and uniquely identified; blockers and nits exactly
   match finding IDs by severity.
4. Outcome, acceptance, and gate mappings cover every contract key exactly once;
   duplicates, unknown keys, invalid statuses, and omissions are rejected. Gate
   commands, metrics, artifacts, sources, and delivery goals remain independently valid.
5. `pass` has no findings; `pass-with-nits` has nit findings only; `reject` has
   at least one blocker finding.
6. Existing `pass-with-nits` flow remains: one worker follow-up and no second
   reviewer. The Worker must submit a new PR revision, different from the reviewed
   revision, with complete fresh evidence; functional, contract, integrity,
   security, or verification defects must therefore be `reject`.
7. A merge command contains both `{pr_url}` and `{delivered_revision}`. The
   default GitHub command uses `--match-head-commit`, so only the current Worker
   delivery accepted by the evidence gate can merge. It equals the Reviewer
   revision for pass and the fresh Worker revision for pass-with-nits follow-up.

### Final acceptance results

1. Top level is a list with no duplicate IDs.
2. Results cover every and only acceptance-document flow ID.
3. Status is `pass` or `fail`; every fail has non-empty notes.

## Common errors → corrections

| Error | Correction |
|---|---|
| Command is similar but not identical to contract | Copy and run the contract command verbatim. |
| Only a “tests passed” summary | Record every command, exit code, gate metric, and artifact. |
| Reviewer reuses worker claims | Rebuild from `env_setup` and record independent mapping. |
| Worker returns fake data to make an error path succeed | Remove the runtime fallback and expose the real error; `runtime_fallbacks` stays empty. |
| Reviewer stops after the first issue | Finish the full revision scope and submit one complete findings batch. |
| Worker or Reviewer reuses evidence from an old commit | Read the current PR head again; Worker updates `delivered_revision` and regression `head_ref`, and Reviewer reports that same revision only. |
| Worker changes to another PR during rework | Keep the same canonical PR resolved by the platform; report a blocker if it cannot continue instead of replacing it. |
| Worker submits a PR number, branch, or `artifacts.pr` | Submit the full canonical GitHub PR URL and use only `artifacts.pr_url`. |
| Duplicate Worker gate evidence hides a failure | Submit each contract gate once and remove duplicate or unknown gates. |
| Duplicate or unknown mapping keys pad coverage | Map every contract key exactly once, remove duplicates and unknown keys, and use a legal status. |
| Merge command contains only the PR URL | Add `{delivered_revision}` and make the platform reject a changed head. |
| Pass verdict retains blockers | Empty blockers; submit reject if a blocker remains. |
| Reject has no actionable reason | State failure fact, effect, and repair entry point in blockers. |
| Final acceptance omits or invents a flow ID | Generate results strictly from the acceptance document. |
| Fail has no explanation | Add non-empty reproducible notes. |

## Submit

Re-read `work show` and use its exact command:

```bash
# worker verification
omac work submit <issue-id> --pr-url <pr-url> --verification-file <file>

# reviewer report
omac work submit <issue-id> --verdict pass --report-file <file>

# final acceptance results
omac work submit <issue-id> --acceptance-results-file <file>
```

Correct structured errors one by one. Do not manually advance platform state.

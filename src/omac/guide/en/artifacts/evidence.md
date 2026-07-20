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
  - cmd: "python3 -m pytest tests/auth"
    exit_code: 0
    summary: "passed"
    business_tests:
      - { acceptance: "flow-login", test: "tests/auth/test_login.py::test_user_can_login" }
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
```

### Reviewer report

```yaml
review_goals:
  - Acceptance is fully covered and each item is verifiable
diff_reviewed: true
tests_rerun: true
integration_tests_rerun: true
coverage_checked: true
full_review_completed: true
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
| `commands[].business_tests` | Concrete business-test indexes executed by this successful command. Each entry contains a contract `acceptance` and stable `test` identifier. The carrying command has a non-empty `cmd` and an integer `exit_code` of `0`; supporting commands may omit the field. |
| `integration_gates` | Command, metric, artifact, source, and delivery-goal evidence by gate name. |
| `pr_base` | Exactly matches contract `pr_base`. |
| `coverage` | Numeric coverage meeting `coverage_gate`. |
| `env_setup` | Reproducible preparation steps; non-empty strings when the contract has integration gates. |

Submit the PR URL separately through `--pr-url`, not in verification YAML.

### Reviewer report

| Field | Meaning |
|---|---|
| `review_goals` | Non-empty list of independently verified review goals. |
| `diff_reviewed` / `tests_rerun` / `coverage_checked` | `true`: the diff was read, tests rerun independently, and coverage checked. |
| `full_review_completed` | `true`: the Reviewer continued after findings, completed the entire review scope, and reported every issue found in that pass. |
| `integration_tests_rerun` | `true` when the contract has integration gates. |
| `acceptance_mapping` | Evidence and pass/fail for every contract acceptance flow. |
| `integration_gate_mapping` | Independently reproduced gate results aligned with the contract. |
| `blockers` | Empty for pass forms; non-empty actionable blockers for reject. |
| `nits` | Non-blocking improvement suggestions. |

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

1. Submit both PR URL and verification file; the GitHub PR is deliverable and not draft.
2. Commands cover the contract's exact commands and exit 0.
3. Every contract acceptance item is covered by concrete `business_tests` on a successful ordinary or integration-gate command; mappings do not reference acceptance items outside the contract.
4. Gate sources and delivery goals exactly match the contract.
5. Metrics meet thresholds and required artifacts are present.
6. With integration gates, `env_setup` is a non-empty list of non-empty strings.
7. PR base matches and coverage is numeric and at least the gate.

### Reviewer report

1. `review_goals` and `acceptance_mapping` are non-empty and cover every contract acceptance.
2. Basic review flags and `full_review_completed` are `true`; integration tests are independently rerun when gates exist.
3. Gate mapping covers each gate and validates commands, metrics, artifacts,
   sources, and delivery goals.
4. Pass forms have no blockers; reject has blockers.

### Final acceptance results

1. Top level is a list with no duplicate IDs.
2. Results cover every and only acceptance-document flow ID.
3. Status is `pass` or `fail`; every fail has non-empty notes.

## Common errors → corrections

| Error | Correction |
|---|---|
| Command is similar but not identical to contract | Copy and run the contract command verbatim. |
| Only a “tests passed” summary | Record every command, exit code, gate metric, and artifact. |
| Verification omits `business_tests`, or treats coverage and mock calls as functional proof | Attach concrete per-acceptance business tests to commands that actually passed; the Reviewer inspects those tests for real behavior. |
| Reviewer reuses worker claims | Rebuild from `env_setup` and record independent mapping. |
| Reviewer submits reject after the first blocker | Record it, finish the entire review scope, set `full_review_completed: true`, and report every blocker and nit found in the pass. |
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

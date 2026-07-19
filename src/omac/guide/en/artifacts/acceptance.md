# Acceptance artifact contract

## When to use it

Use this contract during `acceptance` authoring or review. It turns user-facing
behavior into authoritative flows that workers, reviewers, and final acceptors
can execute item by item.

First run:

```bash
omac work show <issue-id> --output json
```

Its task, context, authority, guide references, and submit command are current
facts. This guide never overrides them.

## Minimum valid example

Submit one directly parseable YAML mapping. Structured fields are the authority:

```yaml
---
schema: omac.acceptance/v1
flows:
  - id: flow-login
    name: A user signs in with valid credentials
    actions:
      - id: open-login
        step: Open the sign-in entry point
        how: Visit /login
        expected: Account and password fields are visible
      - id: submit-valid-credentials
        step: Submit valid credentials
        how: Enter the test account and select Sign in
        expected: The dashboard opens and shows the current user
```

## Field semantics

| Field | Meaning |
|---|---|
| `schema` | Exactly `omac.acceptance/v1`. |
| `flows` | Non-empty list of independently acceptable end-to-end paths. |
| `flow.id` | Unique stable ID referenced by manifest `contract.acceptance` and final results. |
| `flow.name` | Non-empty human-readable user outcome. |
| `actions` | Non-empty ordered actions for the flow. |
| `action.id` | Stable ID unique within the flow. Quality contracts reference it as `flow.id.action.id`, for example `flow-login.open-login`. |
| `step` | The user or system action. |
| `how` | Copyable entry point, command, page, parameter, or test data. |
| `expected` | Observable outcome and the standard for deciding failure. |

Structured YAML is the only authority. Do not append Markdown after the YAML:
the current submit validator parses the whole file as a YAML mapping. Write each
action as self-contained for low-reasoning-budget executors. Model invalid input,
duplicates, permissions, timeouts, and rollback as separate actions or flows.

## Validation gates

1. Top level is a mapping and `flows` is a non-empty list.
2. Each flow is an object with a unique, non-empty string `id` and `name`.
3. Each flow has a non-empty `actions` list.
4. Every action is an object with non-empty string `id`, `step`, `how`, and
   `expected`; action IDs are unique within their flow.
5. Flow and action IDs remain stable. Manifest
   `quality.required_outcomes.source_ref` uses
   `acceptance#<flow.id>.<action.id>`.
6. Flow IDs match design flows and manifest `contract.acceptance`.
7. Success or boundary conditions mentioned only in explanatory prose are not
   machine-verifiable acceptance facts.

## Common errors → corrections

| Error | Correction |
|---|---|
| Empty `flows` or an object instead of a list | Add at least one flow to a list. |
| Multiple flows reuse one ID | Use stable unique IDs and update every reference. |
| An action has no ID or duplicates one in its flow | Assign a stable unique ID and update every `acceptance#flow.action` quality reference. |
| `how: normal operation` | Name page, command, parameters, and test data. |
| `expected: success` | State observable result and failure criterion. |
| Permission failures hidden in prose | Add a dedicated action or flow. |
| Extra prose states a different result | Delete the duplicate fact or correct YAML; retain one authority. |

## Submit

Re-read `work show` and use its exact command:

```bash
omac work submit <issue-id> --acceptance-file <file>
```

The file must parse as YAML directly. Do not submit a verdict during authoring.

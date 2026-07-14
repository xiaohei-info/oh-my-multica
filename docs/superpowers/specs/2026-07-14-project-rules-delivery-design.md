# Project rules delivery design

## Problem

OMAC currently requires a planner to deliver one design document for a `plan`
work item. The document is uploaded through the engine adapter and reviewed, but
the repository does not receive a project-level `AGENTS.md` generated from the
approved design. Later development agents therefore cannot rely on a reviewed,
repository-local set of project constraints.

## Decision

A planner-authored `plan` work item has two required, independently persisted
deliverables:

1. the design document;
2. the project rules document used to maintain the OMAC-managed section of the
   repository-root `AGENTS.md`.

The author submits both in one atomic command:

```bash
omac work submit <issue-id> \
  --plan-file <design.md> \
  --project-rules-file <project-rules.md>
```

Both files are mandatory and non-empty. Validation completes before either
deliverable or any phase/status metadata is written. Missing or invalid input is
a validation failure with exit code 5.

`omac plan create --doc` skips planner authoring and therefore neither requires
nor writes project rules.

## Data model

The existing `deliverable` and `deliverable_ref` fields remain the primary design
document for `TaskKind.PLAN` and retain their current meaning for every other task
kind. `WorkItem` gains two optional fields:

```text
project_rules
project_rules_ref
```

`WorkItemStore.update_work_item_metadata` gains a `project_rules` argument. Mock
storage keeps the text directly. The Multica adapter uploads it as a separate
attachment and stores only the reference in metadata, exactly as it already does
for the primary deliverable. Pipeline and CLI layers continue to consume only the
`WorkItemStore` interface.

This is deliberately narrower than replacing the existing delivery model with a
generic multi-artifact framework. Only `plan` needs the second document, and the
new fields preserve all current callers and stored work items.

## Review and resume

During `plan` review, `work show` exposes the design document and project rules,
including both attachment references. The reviewer contract requires reviewing
them as one decision: project rules must be durable repository-wide constraints,
must agree with the design, and must not contain temporary task instructions.

Reject and pass-with-nits revisions require the planner to submit both files
again. `run_task` returns both values from the reviewed work item:

```json
{
  "plan": "...",
  "project_rules": "..."
}
```

Because both documents are stored by the engine, `plan resume` restores them from
the work item rather than from process memory. A historical plan work item without
project rules cannot pass the new contract; it is returned to planner authoring
for a complete two-file submission and review.

## AGENTS.md ownership and merge

OMAC owns only this marked section:

```md
<!-- OMAC:PROJECT_RULES:START -->
<reviewed project rules>
<!-- OMAC:PROJECT_RULES:END -->
```

The planner submits only the Markdown body. OMAC creates and validates the marker
pair so malformed model output cannot corrupt file structure.

Merge rules:

- missing `AGENTS.md`: create it with the managed section;
- existing file without the section: append the section;
- exactly one valid section: replace it in place;
- duplicate, reversed, or incomplete markers: stop with a validation error and do
  not modify the file;
- all content outside the managed section remains byte-for-byte unchanged;
- repeated execution is idempotent.

The existing root `AGENTS.md`, when present, is included in planner source context
so the new project-rules document preserves established constraints and avoids
contradicting user-owned content.

## Git synchronization

After design, acceptance, and decomposition converge, OMAC updates `AGENTS.md`
and includes it in the existing plan-output commit with the manifest and optional
acceptance document. No extra commit is created. Development agents start only
after this commit/push path, so they clone the reviewed rules.

The original `AGENTS.md` content is snapshotted before planner dispatch. Before
writing, OMAC verifies that the file still matches the snapshot. Concurrent user
changes produce exit code 20 with a structured report; OMAC does not overwrite or
commit them. When Git synchronization is enabled, pre-existing uncommitted
changes to `AGENTS.md` also produce exit code 20 before planner dispatch so the
automatic plan-output commit cannot absorb unrelated user edits.

## Error handling

- missing/empty design or project rules: exit 5 with the exact two-file submit
  command shape;
- malformed existing managed markers: exit 5 without writing;
- concurrent `AGENTS.md` modification: exit 20 with remediation instructions;
- platform attachment upload failure: existing engine `PlatformError`/`AuthError`
  mapping remains unchanged;
- Git synchronization follows the existing plan-output behavior.

## Verification

Tests cover:

- plan authoring rejects a missing or empty project-rules file atomically;
- plan authoring persists and reads both documents in mock and Multica stores;
- `work show` and submit templates expose both artifacts;
- reviewer revisions retain the two-deliverable contract;
- create, append, replace, malformed-marker, idempotency, and concurrent-change
  behavior for `AGENTS.md`;
- `--doc` does not require or update project rules;
- `plan create/resume` includes `AGENTS.md` in the existing plan-output commit;
- the complete test suite remains green.

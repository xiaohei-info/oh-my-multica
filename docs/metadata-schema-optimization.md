# OMAC Metadata Schema Optimization

## Background

OMAC uses platform issue metadata as the durable coordination layer between
human operators, agents, and the deterministic pipeline. Recent live runs showed
that metadata can become noisy or exceed platform limits when long natural
language content is stored directly in metadata.

The concrete example was AITEAM-709. The issue metadata contained stable fields
such as `dag_key`, `kind`, `phase`, `worker`, `reviewer`, `deliverable_ref`, and
`review_report_ref`, but it also contained:

- a full `review_report` object with reviewer-written goals, evidence, mapping
  explanations, verification summaries, nits, and free-form prose;
- `decision_required.nits[].issue` and `decision_required.nits[].fix`, which are
  structured as JSON but still contain unpredictable reviewer-authored natural
  language.

The goal is not to remove every large field. A large but fixed-schema,
programmatically produced object can remain metadata if it is a real indexable
fact. The goal is to stop storing arbitrary prose, Markdown, long reports, and
unbounded content in metadata.

## Core Decision

Metadata is an index and state table. It is not a content store.

Metadata may contain:

- stable IDs;
- phase and status facts;
- bounded counters;
- fixed enums;
- short machine-readable references;
- small programmatically generated summaries whose schema and size are bounded.

Metadata must not contain:

- Markdown documents;
- design documents or acceptance documents;
- full deliverables;
- full verification evidence;
- full review reports;
- agent self-narration;
- reviewer free-form prose such as evidence paragraphs, nit descriptions, or
  fix suggestions;
- unbounded strings copied from user input, agent output, command output, or
  comments.

Long content must be stored as issue attachments or other payload comments and
referenced from metadata by a stable `*_ref` object.

## Metadata Field Naming

Multica metadata is a flat key-value store, so names must carry ownership and
phase context. Avoid adding generic keys that can be written by many stages.

### Existing Compatibility Keys

These existing keys should continue to be read for backward compatibility:

| Key | Status | Notes |
| --- | --- | --- |
| `dag_key` | keep | Stable issue/DAG locator. |
| `kind` | keep | Existing task kind. Future alias may be `task_kind`. |
| `phase` | keep | Existing task phase. Future alias may be `task_phase`. |
| `worker` | keep | Current/authoring worker name. |
| `reviewer` | keep | Current reviewer name. |
| `blocked_by` | keep | Stable DAG references. |
| `ci_bounce` | keep | Bounded counter. |
| `review_bounce` | keep | Bounded counter. |
| `merge_bounce` | keep | Bounded counter. |
| `review_verdict` | keep | Stable enum. |
| `deliverable_ref` | keep | Stable payload reference. |
| `verification_ref` | keep | Stable payload reference. |
| `review_report_ref` | keep | Stable payload reference. |
| `decision_required` | keep but compact | Must contain only bounded machine facts and refs. |
| `contract` | keep for now | Fixed-schema contract can remain until it becomes a size issue. |

### New Naming Convention

For newly introduced keys, use phase-scoped prefixes:

| Prefix | Owner | Examples |
| --- | --- | --- |
| `task_*` | task identity | `task_kind`, `task_phase` |
| `authoring_*` | producer phase | `authoring_deliverable_ref` |
| `verification_*` | worker evidence | `verification_ref`, `verification_status`, `verification_command_count` |
| `review_*` | reviewer phase | `review_verdict`, `review_report_ref`, `review_blocker_count`, `review_nit_count` |
| `decision_*` | human decision gate | `decision_required`, `decision_reason`, `decision_report_ref` |
| `ci_*` | CI loop | `ci_bounce`, `ci_status` |
| `merge_*` | merge loop | `merge_bounce`, `merge_status` |

Do not introduce keys like `summary`, `notes`, `comment`, `report`,
`evidence`, or `result` without a phase prefix and a bounded schema.

## Reference Object Schema

All long payload references should use the same shape:

```json
{
  "comment_id": "uuid",
  "attachment_id": "uuid",
  "sha256": "hex",
  "bytes": 1234,
  "filename": "omac-review-report-abcdef.yaml"
}
```

Rules:

- `sha256` and `bytes` are mandatory when an attachment exists.
- `filename` should be deterministic enough for debugging, but consumers must
  not depend on the hash prefix length.
- `comment_id` and `attachment_id` are platform locators, not content.
- Consumers should verify `sha256` when downloading payloads for review or
  replay.

## Target Metadata by Stage

### Task Creation

Writers:

- `WorkItemStore.create_work_item`
- `pipeline.loop._dispatch`
- `pipeline.tasks.run_task`

Allowed metadata:

- `dag_key`
- `kind`
- `phase`
- `worker`
- `reviewer`
- `blocked_by`
- `wave`
- optionally `contract`

Review:

- `description` is not metadata. It is Human-first issue body content and may contain
  one Agent `work show --output json` bootstrap, Markdown task context, and upstream links.
- `contract` is currently fixed-schema and programmatic. It can remain metadata
  for now.
- If contract begins to carry large upstream prose, introduce `contract_ref`
  before expanding usage.

### Authoring Submit

Writers:

- `omac work submit --plan-file`
- `omac work submit --acceptance-file`
- `omac work submit --manifest-file`
- `omac work submit --acceptance-results-file`

Allowed metadata:

- `phase`
- `deliverable_ref`

Forbidden metadata:

- `deliverable`
- Markdown plan text
- acceptance document text
- manifest YAML text
- acceptance result text

Current direction:

- The Multica store already publishes deliverables as attachment comments and
  writes `deliverable_ref`. Keep this behavior.
- Continue to read legacy inline `deliverable` for backward compatibility only.

### Develop Authoring Submit

Writers:

- `omac work submit --pr-url --verification-file`

Allowed metadata:

- `artifacts.pr_url`
- `verification_ref`
- optional bounded facts:
  - `verification_command_count`
  - `verification_failed_count`
  - `verification_status`

Forbidden metadata:

- full `verification`
- command output logs
- environment setup prose
- agent-written verification summaries

Required change:

- Stop writing full `verification` metadata in `MulticaStore`.
- Keep reading legacy inline `verification`.
- When `verification_ref` exists, load and parse the attached YAML/JSON to
  populate `WorkItem.verification`.

### Review Submit

Writers:

- `omac work submit --verdict --report-file`

Allowed metadata:

- `review_verdict`
- `review_report_ref`
- `review_blocker_count`
- `review_nit_count`
- optional fixed booleans:
  - `review_diff_reviewed`
  - `review_tests_rerun`
  - `review_coverage_checked`

Forbidden metadata:

- full `review_report`
- `review_goals`
- `acceptance_mapping[*].evidence`
- `integration_gate_mapping[*].evidence`
- `independent_verification.commands[*].summary`
- `nits[*].issue`
- `nits[*].fix`
- `blockers[*]` free-form text
- any reviewer prose

Required change:

- Stop writing full `review_report` metadata in `MulticaStore`.
- Keep reading legacy inline `review_report`.
- When `review_report_ref` exists, load and parse the attached YAML/JSON to
  populate `WorkItem.review_report`.

### Pass With Nits

Writers:

- `pipeline.tasks.run_task`
- `pipeline.loop.collect_results`

Default behavior:

- `pass-with-nits` is not a failure gate.
- The pipeline returns the issue to the worker so the producer can address the
  non-blocking review suggestions.
- This does not increment `review_bounce`.
- This does not write `decision_required`.

Allowed metadata during the handoff:

- clear `review_verdict`;
- clear `review_comment`;
- clear/empty `decision_required`;
- set `phase=authoring`;
- keep `review_report_ref` so `work show` can surface the previous review.

If a future human decision gate needs to represent pass-with-nits, the allowed
shape is counts plus refs only:

```json
{
  "kind": "decompose",
  "phase": "review",
  "verdict": "pass-with-nits",
  "round": 1,
  "blocker_count": 0,
  "nit_count": 1,
  "review_report_ref": {}
}
```

Forbidden `decision_required` fields:

- `review_report`
- `blockers` with natural language content
- `nits` with natural language content
- `issue`
- `fix`
- `evidence`
- `summary`

If a UI or CLI needs nit details, it must load `review_report_ref`.

### Retry / Reset Review

Writers:

- `WorkItemStore.reset_review`
- retry paths in `pipeline.loop`

Allowed metadata:

- clear `review_verdict`
- clear `review_comment`
- clear/empty `decision_required`
- set `phase=authoring`
- increment `review_bounce`

Review:

- This is already small and bounded.
- Prefer `{}` for `decision_required` over string `"{}"` internally. Store
  adapters can encode as needed.

### CI and Merge Loops

Writers:

- `pipeline.delivery.advance_delivery`
- `pipeline.delivery.run_merge_delivery`

Allowed metadata:

- `ci_bounce`
- `merge_bounce`
- optional bounded status fields in the future.

Forbidden metadata:

- full CI logs;
- merge conflict patches;
- long command output.

Long diagnostics should be comments or attachments, not metadata.

## Store-Level Rules

Add a small metadata policy in the Multica store instead of scattering checks
through pipeline code.

### Write Policy

Before `_set_metadata`, classify the key:

- scalar allowed keys: strings/enums/counters only;
- ref allowed keys: must match the reference object schema;
- structured allowed keys: must be bounded and schema-controlled;
- legacy inline keys: allowed for reading, not for new writes.

Suggested implementation:

- `metadata_policy.py`
  - `encode_metadata_value(key, value) -> str`
  - `assert_metadata_write_allowed(key, value) -> None`
  - `summarize_review_report(report) -> dict`
  - `summarize_verification(verification) -> dict`

Keep this simple. Do not build a framework.

### Read Policy

Read order for payloads:

1. Prefer `*_ref` and load attached YAML/JSON.
2. Fall back to legacy inline metadata.
3. If parsing fails, return a structured raw fallback only for legacy data:

```json
{"raw": "..."}
```

For new writes, never create `raw` metadata values.

## Compatibility Plan

This must not break existing issues.

Read compatibility:

- Continue reading old `deliverable`, `verification`, `review_report`, and
  `decision_required.nits`.
- If both inline and ref exist, prefer ref because it is the source of truth for
  new writes.
- Existing old issues should still resume.

Write behavior:

- New writes must use refs and bounded metadata only.
- Do not delete legacy metadata from existing issues automatically.
- Optional cleanup can be a separate maintenance command later.

## Tests Required

Add tests at the store and pipeline level.

### Multica Store Tests

Use the existing fake Multica process in `tests/test_taskmeta.py`.

Required assertions:

- `update_work_item_metadata(review_report=..., review_report_source=...)`
  writes `review_report_ref` but does not write `review_report`.
- `update_work_item_metadata(verification=..., verification_source=...)`
  writes `verification_ref` but does not write `verification`.
- `get_work_item()` reconstructs `review_report` from `review_report_ref`.
- `get_work_item()` reconstructs `verification` from `verification_ref`.
- YAML and JSON payload files both parse correctly.

### Decision Metadata Tests

If a path writes `decision_required`, test that:

- `decision_required` contains `nit_count` and `blocker_count`.
- `decision_required` does not contain `nits`, `blockers`, `issue`, `fix`,
  `summary`, or `review_report`.
- `decision_required.review_report_ref` is present when available.

### End-to-End CLI Tests

For `omac work submit`:

- review submit with a report containing long free-form text must succeed;
- metadata should contain only `review_report_ref`, counts, and verdict;
- `omac work show` must still display full review context by loading the
  attachment.

For develop submit:

- verification file with long summaries must succeed;
- metadata should contain only `verification_ref` and short artifacts;
- review phase still receives `env_setup` via loaded verification payload.

## Acceptance Criteria

An implementation is acceptable when:

- no new Multica metadata write stores full `review_report`;
- no new Multica metadata write stores full `verification`;
- pass-with-nits returns to worker without writing natural-language decision metadata;
- any future `decision_required` stores counts and refs only;
- `deliverable` remains ref-based;
- existing issues with inline legacy metadata still read correctly;
- `omac work show`, `dag status`, `plan resume`, and `node accept` continue to
  work against old and new issues;
- full test suite passes;
- a live run similar to AITEAM-709 produces metadata containing only stable
  state fields, refs, counters, and fixed enums.

## Non-Goals

- Do not redesign the whole issue model.
- Do not migrate historical issues automatically.
- Do not remove issue descriptions or comments; they are content surfaces, not
  metadata.
- Do not move fixed-schema `contract` out of metadata in the first pass unless
  it causes platform size errors.
- Do not rename all existing keys immediately. Add aliases only when a future
  migration requires them.

## Suggested Implementation Order

1. Add metadata policy helpers and tests.
2. Stop writing inline `review_report`; load from `review_report_ref`.
3. Stop writing inline `verification`; load from `verification_ref`.
4. Ensure pass-with-nits handoff writes no natural-language decision metadata.
5. Add regression tests for forbidden natural-language metadata fields.
6. Run the full test suite.
7. Validate with one live Multica issue.

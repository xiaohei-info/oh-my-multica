# Runtime-backed detailed-design delivery pass

Use this reference when a runtime-backed product/control-plane detailed-design master document is already structurally sound and now needs a final pass to become delivery-ready.

## Trigger
- The overview design is already accepted.
- The detailed-design master note already covers scope, object mapping, and main flows.
- The user asks to "keep going until it can be delivered", "finish the later sections too", or otherwise signals that a conceptually-correct draft is not enough.

## Final-pass checklist

### 1. Runtime/control-plane structure skeleton
Add one compact structure view (ASCII is enough) that separates:
- product web/UI layer
- product API / control plane
- gateway adapter / translation layer
- reused host shell / WebUI scaffold if any
- runtime layer
- external capability layer

This prevents the document from reading like one blurred repo/module.

### 2. Runtime handle / reconcile key
Define a smallest-possible handle object for business-to-runtime reconciliation.
Typical fields:
- `profile_name`
- `session_id`
- `task_id`
- `job_id`
- `event_cursor`
- `last_synced_at`

Rule: every business `Run` / `Task` / `ScheduledJob` should be able to point to at least one runtime truth handle.

### 3. Replayable event envelope
If the UI must show progress, do not stop at "SSE stream".
Define:
- a replayable event envelope
- a minimum event enum list
- cursor semantics for reconnect/replay

Good default event families:
- run created / routing decided
- orchestrator started
- task spawned / assigned / started / completed / failed
- stream delta
- result merged
- usage recorded
- run finished

### 4. Status enums with execution semantics
For core product objects, do not only list enum names.
Explain what each state means at runtime and who moves it.
Especially for:
- employee lifecycle
- conversation lifecycle
- run lifecycle
- scheduled job lifecycle

### 5. Credential/auth resolution path
When connectors or external tools exist, define the path clearly:
- product truth = `credential_ref` + grants
- secure storage = secret store / equivalent
- gateway = resolution + injection policy
- runtime = receives resolved visibility/injection result, not product-layer secret truth

This is the fastest way to avoid vague connector prose.

### 6. Failure / retry / compensation closure
Add one dedicated section covering:
- idempotency keys on create/start operations
- runtime not yet accepted vs already accepted failures
- stream disconnect and cursor replay
- child worker failure under orchestrator
- scheduled-job repeated failure and pause/escalation path

If a master document has only happy paths, it is not delivery-ready.

### 7. Northbound contract examples
Keep reused host routes demoted.
But add concrete examples for:
- product northbound request/response
- internal adapter call shape
- timeline event payload shape

The goal is implementation concreteness without over-coupling to reused internals.

### 8. NFR / security / backup / reconcile section
Minimum topics to force explicit:
- latency / first-visible-event targets
- timeline replay depth or volume assumption
- audit tags / correlation keys
- tenant isolation constraints
- secret handling constraints
- backup scope and restore/reconcile path

### 9. Phase order and acceptance bar
End the master note with:
- recommended phase order
- per-phase or overall acceptance checklist

If the document lacks delivery order and acceptance, it is still mainly a design review artifact.

## Fast rejection tests
Reject the detailed-design master note as not-yet-deliverable if any of these are true:
- no runtime handle / reconcile key exists
- event streaming is mentioned but no replayable event envelope is defined
- connector/credential prose does not say where secret truth lives
- status fields exist but execution semantics are absent
- later sections never explain failure closure, NFR, backup/reconcile, or acceptance

## Typical patch targets inside the note
When tightening an existing master note, the fastest high-value insertion points are:
- after design principles: add runtime/control-plane skeleton
- after core objects: add runtime handle + status semantics + credential resolution plan
- after main flows: add failure/retry/compensation closure
- in mapping section: add connector/auth mapping and reconcile mapping
- near the end: replace generic risk-only ending with interface contracts, NFR/security/recovery, implementation order, acceptance checklist


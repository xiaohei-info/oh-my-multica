## Assessment stopped: Source and publication authority gaps

Issue: AITEAM-1080 (`01a11f7b-d5ac-73a8-972c-7939d1458ccf`). This is a partial, read-only scope assessment, not a valid amendment proposal, independent review, Product PASS, implementation authorization, or apply/recovery instruction.

Requested owner resolution: `3f706c3a28b2730417030ad99998e907ac29884c0423a50351e19d8779844c0b`.
Requested approval: `176e36b06eac76ebe3f05eb2565ba26cf2291fab73e1836d1453948713baa1d0`.
Required budget policy remains `preserve`; no new budget authority is inferred.

### Verified facts

- The current supported `omac work show` for credential-store identifies issue `01a10384-778a-7d81-aacf-09c8da085586`, authoring/blocked, actual worker bounce 0 and review bounce 2, and the retained owner-decision-required blocker. Its binding is generation `authoring-6e52e8a37eebf435b3e10373`, contract SHA `839d8002ba6367b0e903dc42c19a908b50c54f07f6de26a81992bab788cfe3bc`.
- The current contract's sole declared external input is `contracts-credential-wire-component` from `contracts-credential`. Primary consumer scope is `internal/credentials/**`. The contract prohibits changing adjacent ownership and bypassing authorization, admission, CAS, idempotency, review, or evidence gates.
- `CredentialRotationRecord.ExpiresAt` and its digest projection use a required `time.Time`; `validateShape` rejects zero expiry (`contracts/governance/credential/wire.go:312-345`). These wire bytes are identical in the fetched manifest-runtime and main commits; SHA256 is `dbc02ed64ceea60923248ad73f7254b7573b1909f0354f599a0fd9e1e9417c5c`.
- The inspected design specifies optional rotation expiry, non-auto-expiring App private keys, and provider-expiry-bounded installation tokens. `UJ-CRED-001` requires conditional expiry and immutable rotation/audit evidence without changing Binding identity, losing CAS, or exposing secrets. Source ownership separately assigns `contracts/governance/credential` to `contracts-credential` and `internal/credentials` to `credential-store`.
- In fetched main commit `015423376b043f36dc28520a208a4eaa6d9da09d`, contracts-credential is DONE/merged and credential-store carries the exact current issue ID. That published manifest does not contain the requested owner resolution. Its raw manifest SHA256 is `f09a5f4ffec61723f8b8fa17b3d3930ed2918d23580b4d033ce615e4dcf73752`.
- The specified PR base manifest-runtime, commit `dee38b8ea0664fe4a171f0d01aecc099c9f19148`, is not a current runtime baseline: credential-store has no work_item_id there. It likewise lacks the requested resolution. Its manifest SHA256 is `c3e46f2a42a612b719e275a819e3eb18cc861c05af4ab410d75e9a604a908dfc`. Its overview, governance detailed design and security reference also differ from fetched main. No baseline was silently selected or repaired.
- Both original independent review reports and both ledgers were downloaded using the authenticated Multica attachment CLI. All exact sizes and SHA256 values matched their native attachment references: reports `e4b07b7083f22f898999957abbf57eba3763b2746d9bc40425cf51dbfe0e8410` (16210 bytes), `d4b44c80ad70cb6d63231afb8af079316bccbaf55a5f9cf9ab0d00fc3bf0e321` (21914 bytes); ledgers `0051be84c9506ac0a970adc791983bde1766858d1545f5b83d0a6e506138fe5d` (11359 bytes), `df637f5349894ccd92cea533d21dad5678e3d0383c2db267b5fdaed6a517550b` (30595 bytes). This integrity check does not substitute for the complete owner-request/native causal Source or establish technical closure.
- `omac dag check .omac/open-agent-cluster.yaml --no-review` passed on the manifest-runtime checkout (172 nodes). This is only that published snapshot's lint result, not current Source/CAS validation or amendment validation.

### Source gap

The contract names `new-full-owner-request.json` under an operator-local `/Users/chiangguantik/.../Root-Credential-current-owner-assessment-Source-20261009/` directory. That exact file is absent on this runtime; the assigned issue exposes no attachment containing it. Neither inspected published manifest contains its requested resolution/approval. The full approved request, canonical live manifest, full native causal Run history, effective approved budget baseline/limits/remaining, and frozen ALL-DONE/history comparison cannot therefore be authenticated from these inputs. Native None is retained as a reported source fact, not converted to zero or a grant.

### Concrete authority gap and conditional minimum boundary

Governance is the common semantic owner, but the DAG separates publication from consumption. A credential-store-only change cannot lawfully publish a replacement for an independently owned, already published wire contract. Silently changing the consumed validator or digest semantics, inventing expiry, requiring App-key expiry for convenience, reopening the DONE producer, or allowing undeclared inputs would violate the task.

The smallest candidate boundary requiring a new exact Root decision is:

1. An explicitly owned compensating publication for the conditional-expiry rotation wire, with immutable old bytes/digests and stored-record interpretation preserved; reviewed compatibility/encoding/validation and type-specific negative fixtures; a separately identifiable published artifact. Keep contracts-credential's DONE record untouched. No new node ID, worker, artifact allocation or implementation is authorized by this assessment.
2. Only after that publication boundary is authorized and delivered, adapt credential-store's declared dependency/input and owner implementation to it, preserving every existing acceptance reference, owner-proof/CAS/idempotency/audit/materialization/least-privilege obligation and all original rejected evidence. Non-expiring App-key rotation and expiring PAT behavior require fresh evidence; the worker's other reported fixes are not independently passed here.
3. Compute the actual downstream recovery set from the full approved live manifest and reject every changed/derived target not explicitly authorized. Do not add UI/System/SDK/Conformance work for coordination convenience.

Conditional stage rationale: a new compensating producer requires authoring because the corrected publication does not exist; credential-store requires authoring after that publication because aligned rotation behavior remains unimplemented, followed by independent review. Neither unchanged review nor merging can resolve this wire defect. These are planning conditions, not submitted resume operations or a claim that the complete derived set is known.

### Required handoff

Provide the immutable approved owner request and its complete Source package plus the matching current manifest/docs through a supported accessible handoff. Root must then explicitly authorize the exact compensating producer/publication ownership and every actual changed/derived target, retaining `budget_policy: preserve`. Normal Planner and one fresh independent OAC Reviewer must assess the resulting complete proposal; apply/recovery remains a separate Root decision.

No product code, manifest, issue status, budget, DONE fact, stored record, assignment, or recovery receipt was changed. No credential operation was executed. No amendment was submitted: the supported schema requires nonempty real operations, and the current OMAC guide explicitly does not implement an amendment scope-block protocol. A no-op/resume or fabricated operation would conceal rather than resolve these gaps. Direct platform comment/status writes were not used because this task's OMAC-only authority forbids that fallback.

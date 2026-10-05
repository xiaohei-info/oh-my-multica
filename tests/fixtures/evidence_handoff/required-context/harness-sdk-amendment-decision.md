# harness-sdk amendment decision

## Decision

Keep the public `harness.oac.dev/v1` SDK as a protocol-neutral typed boundary for Describe, execute, observe, cancel, scoped permission requests, and Unknown recovery. It consumes the published extension wire contract and must not invent a second Host or governance authority.

## Required completion language

1. Describe, execute, observe, and cancel use typed request/result/error unions with explicit external operation identity, scope, permissions, and Unknown/Lost recovery semantics. No nullable field bag or raw map may bypass validation.
2. `HostScopeAttestation` has a complete public service-mode issuance/transport representation bound to the authenticated RequestScope, selected Package/entrypoint, exact operation, owner, and digest; callers cannot self-attest Host authority.
3. Permission requests and effects are validated against the verified Package/Describe contract and the authoritative authorization/admission boundary. Missing, stale, ambiguous, or mismatched evidence fails closed before an external effect.
4. Describe schemas and every non-executor extension point are validated by the declared conformance suite. The suite must cover positive execute/observe/cancel, cancellation/Unknown recovery, unsupported effects, missing attestation, mismatched scope/digest, malformed Describe output, and safe retry/cancel behavior.
5. Fix the declared Go coverage command to tolerate the actual tool output and make the raw gate reproducible; do not use an undeclared shim.
6. Preserve the single Harness execution boundary: the SDK surrounds one primary execution and does not become a second executor, lifecycle controller, persistence store, or generic plugin runtime.

## Scope and non-goals

- Scope is limited to `sdk/harness/**`, its exact conformance tests/evidence, and contract-selected packaging files.
- Consume `contracts-extension-wire` and the approved governance/authentication/admission ports; do not redefine them.
- Do not add a public secret/credential material path, a second audit authority, or a new execution mode.
- Unknown external outcomes remain Unknown and require observation/recovery evidence; never convert timeout or lost response into business failure.

## Authority

- `docs/design/open-agent-cluster-overview-design.md`
- `docs/design/detailed/08-component-and-extension-detailed-design.md`
- `docs/design/detailed/09-governance-and-security-detailed-design.md`
- `docs/references/extension/plugin-contracts.md`
- Latest reviewer report supplied with this amendment proposal

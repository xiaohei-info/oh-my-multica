# model-catalog amendment decision

## Decision

Keep Model Catalog as the owner of provider connection configuration, discovery snapshots, connectivity probes, exact model selection, and their immutable provenance. Close its governance and concurrency boundaries without duplicating the separately owned model-manual contract or moving authorization/audit ownership into the catalog.

## Required completion language

1. Production construction and every mutating/query operation require authenticated RequestContext, scope, authorization/admission and canonical audit evidence. Test constructors must be test-only or explicitly non-production and cannot expose a production bypass.
2. Provider egress policy is mandatory and scope-bound before credential resolution/injection; absent policy, unsupported origin, redirect, or incompatible provider/driver fails closed.
3. Non-secret headers use a typed validated projection; arbitrary caller strings cannot become durable credential-like state or digest inputs. Secret material remains behind CredentialBinding.
4. Audit facts and catalog mutations are transactionally bound or represented by an idempotent durable outbox. Every claimed provider attempt reaches a durable terminal observation, including Unknown on post-attempt persistence failure; no permanent in-progress claim is left without recovery.
5. Select one owner for manual model definitions. The catalog must either consume an explicit conformance artifact from model-manual or remove the duplicate contract; do not maintain two independent snapshot/digest authorities.
6. Snapshot publication requires CAS on the current connection revision/configuration digest and predecessor. Probe evidence keys include snapshot identity so two snapshots cannot overwrite each other's evidence; stale in-flight discoveries fail closed.
7. Add permanent positive/negative tests for governance provenance, egress/credential ordering, typed header redaction, audit transaction/outbox, revision CAS, snapshot-scoped probes, legacy writer rejection, and post-attempt recovery. The exact gate must measure these behaviors.

## Scope and non-goals

- Scope is limited to `internal/modelcatalog/core/**` and `internal/modelcatalog/providers/**`, plus exact tests/evidence needed for this contract.
- Do not change model-manual ownership silently; any ownership migration must be an explicit amendment operation with catalog and artifact evidence.
- Do not add a generic provider/plugin runtime, return Secret material, or make an external provider the source of truth.

## Authority

- `docs/design/open-agent-cluster-overview-design.md`
- `docs/design/detailed/09-governance-and-security-detailed-design.md`
- `docs/references/governance/security-and-enterprise.md`
- Latest reviewer report supplied with this amendment proposal

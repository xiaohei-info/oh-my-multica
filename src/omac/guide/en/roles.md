# Agent role index

This page only selects a role guide. It does not contain each role's complete
protocol. Current task facts, identity, and `guide_refs` from
`omac work show <issue-id> --output json` always decide the work.

| Lifecycle role | When it appears | Main output | Read |
|---|---|---|---|
| planner | `plan` / `acceptance` authoring | Design and acceptance documents | `omac guide role planner` |
| orchestrator | After design and acceptance pass; after final-acceptance failure | Manifest DAG and incremental fix nodes | `omac guide role orchestrator` |
| worker | `develop` authoring | PR and verification | `omac guide role worker` |
| reviewer | Review phase for plan, acceptance, decompose, or develop | Verdict and review report | `omac guide role reviewer` |
| acceptor | After the inner DAG converges | Final acceptance results | `omac guide role acceptor` |

## Selection rules

1. Read `work show.task.identity` and `work show.task.phase`.
2. Run the commands listed in `work show.guide_refs`.
3. When a role guide conflicts with instance context, follow instance facts and
   the contract.

## Responsibility boundaries

- Planner writes design and acceptance documents; it does not split the DAG or
  write product code.
- Orchestrator decomposes manifests; it does not implement product code.
- Worker implements the contract; it does not self-review or self-approve.
- Reviewer independently verifies; it does not edit the author's deliverable.
- Acceptor executes acceptance flows end to end; it does not bypass unverified
  work.

## Architect capability profile

Architect is not a sixth lifecycle role. It is an agent capability profile that
can be assigned as planner, orchestrator, or architecture reviewer. As planner,
follow `omac guide role planner`; as orchestrator, follow
`omac guide role orchestrator`.

Architect work focuses on module boundaries, data flow, dependency direction,
cross-module contracts, ADRs, and architecture drift, while remaining bound by
the current issue facts and role boundary.

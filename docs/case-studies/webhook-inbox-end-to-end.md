# Webhook Inbox: a real end-to-end delivery

On July 16, 2026, oh-my-multica took one production-constrained requirement and
delivered a working Webhook Inbox through design, implementation, independent
review, merge, and final acceptance.

The result is public in the
[demo repository](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox).
It is a small FastAPI and SQLite service, but the delivery constraints are real:
HMAC-SHA256 authentication, exact-byte idempotency, transaction-safe database
deduplication, bounded request bodies, stable errors, hashed dependencies,
Python 3.10–3.13 CI, and a non-root container with a healthcheck.

This was not a scripted mock run. Multica hosted the work items and Coding Agent
runtimes. oh-my-multica planned and controlled the delivery until the integrated
default branch passed the approved acceptance document.

## From one goal to a delivery DAG

The input was a single checked-in [delivery goal](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/GOAL.md).
Planner and Orchestrator Agents inspected the repository, produced the design
and acceptance definition, and dynamically authored a five-node
[manifest DAG](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/.omac/webhook-inbox.yaml):

| Node | Delivery boundary | Public result |
| --- | --- | --- |
| Shared foundation | Domain types, configuration, errors, quality baseline | [PR #2](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/2) |
| HTTP API | Bounded raw-body reads, headers, stable errors, health endpoint | [PR #3](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/3) |
| Persistence and dedup | Verify-before-parse service flow and transaction-safe SQLite deduplication | [PR #4](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/4) |
| Delivery assets | Hashed dependencies, CI matrix, Docker image, operator documentation | [PR #5](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/5) |
| Integration acceptance | Full-path harness and cross-track closure | [PR #6](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/pull/6) |

The dependencies reflected the architecture. The API and persistence tracks
could run in parallel after the shared contracts existed. Delivery assets
needed both tracks, and integration acceptance waited for the assembled
service. An earlier foundation attempt, PR #1, was superseded and closed rather
than being counted as a successful delivery.

## Role and model allocation

The repository's checked-in configuration records the role split:

- `codex-ubuntu` handled planning, orchestration, and final acceptance.
- Three `newapi` Worker runtimes formed the cost-efficient implementation pool.
- `hermes-reviewer` was the primary independent Reviewer, with a separate
  secondary Reviewer available.
- The Loop allowed at most three nodes to run in parallel and kept explicit
  retry and acceptance-round bounds.

This is the practical reason to separate dynamic planning from deterministic
progression. Strong reasoning capacity can be concentrated on architecture,
decomposition, review, and acceptance. The larger volume of bounded coding and
testing work can be assigned to cheaper runtimes without letting those Workers
decide whether the whole project is complete.

## What the Loop controlled

After the design, acceptance document, project rules, and manifest passed their
gates, deterministic software owned the outer delivery Loop:

```text
collect results → compute ready nodes → dispatch → verify evidence → review
→ bounded rework → merge → final acceptance → stop only after convergence
```

Workers retained freedom inside their task contracts. They could inspect code,
choose an implementation, run tests, and open Pull Requests. The Loop retained
authority over dependencies, evidence requirements, review handoffs, retry
bounds, merge eligibility, recovery, and the final exit code.

## The first final acceptance failed

All implementation Pull Requests had merged, and the production service's own
acceptance harness passed. The project still did not converge on the first
final-acceptance round.

The reviewed acceptance document started an intentionally minimal
`src.api:app` stub, while the production composition root was `compose:app`.
The Acceptor executed the document literally and recorded two passing flows and
nine failed flows. It did not replace that evidence with a Worker summary or
infer that the integrated service was probably correct.

The source was corrected in
[commit `56daf00`](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/commit/56daf007c2cd6fc1b25c03e22ad4e957d18ea2a3).
The complete acceptance document then ran again from the beginning. All 11
flows passed, including concurrent same-ID delivery, persistence across restart,
body limits, authentication failures, conflicts, retrieval, and database
health. The controller returned exit 0 only after that second round.

That failure is more useful than a clean demo path. Code generation had already
finished, but the evidence source and production entry point disagreed. The
Loop preserved the failed result, refused completion, and required the source
of truth to be repaired before rerunning acceptance.

## Final evidence

| Evidence | Observed result |
| --- | ---: |
| DAG convergence | 5/5 nodes `done` |
| Reviewed changes | 5 Pull Requests merged |
| Test suite | 86 tests passed |
| Coverage | 97.18% |
| CI compatibility | Python 3.10, 3.11, 3.12, and 3.13 |
| Container delivery | Non-root image, healthcheck, signed-webhook smoke test |
| Final acceptance | 11/11 flows passed |
| Controller | exit 0 |

The [acceptance document](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/blob/main/.omac/webhook-inbox.acceptance.yaml),
manifest, source, tests, CI history, Pull Requests, and failed-then-corrected
commit history are all public. The demo README contains copyable reproduction
commands.

## What this case does and does not show

This case shows that oh-my-multica can dynamically plan a small production-style
service, split it into independently verifiable work, use multiple Agent
runtimes, preserve failed evidence, and converge only after integrated
acceptance.

It does not show that every Agent result is correct on its first attempt, that
all repositories need five nodes, or that deterministic control can repair a
bad requirement by itself. It shows a narrower and more useful property: when
implementation is delegated broadly, project completion does not have to be a
guess made by the last Agent still running.


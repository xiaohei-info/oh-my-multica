# Exit 20 recovery protocol (Controller Agent)

When `omac dag run` returns exit 20, the deterministic engine needs a caller
decision. This is neither success nor an ordinary error to retry silently. The
structured stdout report is the current recovery fact.

## Authority order

1. The exit 20 report and `omac dag status <manifest> --output json`.
2. The node evidence chain from `omac node show <manifest> <key>`.
3. If a node has an issue, its `omac work show <issue-id> --output json` context.
4. Manifest contract and previous review.
5. This recovery guide.

## Decision flow

1. Run `omac dag status <manifest> --output json` for the complete snapshot.
2. For every unresolved node, run `omac node show <manifest> <key>` and read
   verification output, reviewer report, PR, platform issue link, and bounce count.
3. Choose an explicit action:
   - `omac node retry <manifest> <key> [--worker <replacement>]`: reset to todo.
   - `omac node accept <manifest> <key>`: accept a known risk and mark done.
   - `omac node abandon <manifest> <key>`: abandon the node and unlock downstream
     work that does not hard-depend on its deliverable.
   - Change the manifest: repair a contract, change assignment, or split a node;
     run `omac dag check` when needed.
4. Re-run `omac dag run <manifest>`. Completed nodes are reused; the remainder
   continues from current state.

## Choosing an action

| Signal | Inspect first | Usual action |
|---|---|---|
| `reviewer reject` | `report.blockers`, real diff, failed commands | Repair the node, then `omac node retry` |
| CI failure | CI log, `verification.commands` | Repair CI and retry; repair the contract or split if it is unsound |
| Merge retries exhausted | PR base, conflict files, integration branch | Reassign and retry, or resolve the conflict then rerun |
| `acceptance.max_rounds` exhausted | Failed-flow list, incremental manifest | Reduce scope, add nodes, or explicitly accept/abandon |

`accept` accepts a known risk; it does not skip failed verification. `retry`
requires new evidence or a new plan, not the same failed attempt.

## Agent versus Human decisions

A Controller Agent may retry without changing goals, contracts, or risk
acceptance—for example, reassigning to a better worker, repairing from an
existing blocker, or splitting a coarse node into behaviorally equivalent nodes.

Ask a Human before accepting failed verification or risk; abandoning a
user-visible capability or incomplete downstream acceptance scope; deleting an
acceptance flow; relaxing non-goals, coverage, integration gates, or product
scope; choosing between options with different compatibility, cost, migration,
or security consequences; or acting without required credentials, authorization,
or business decisions.

The request must include unresolved nodes, failure facts, commands run, blocked
downstream nodes, options, risk per option, and a recommendation—not merely
“confirmation needed.”

## Abandon semantics

`abandon` is explicit: the node no longer advances, but an abandoned upstream
counts as a satisfied dependency. Downstream work that does not hard-depend on
its deliverable may enter the ready-node set in the next round.

- Downstream nodes continue without waiting for the abandoned deliverable.
- Reports mark descendants of abandoned nodes because acceptance scope may be
  incomplete.
- `omac node retry` can restore the node to todo if the decision is reversed.

Use it for low-value repeatedly failing optional capabilities or experimental
integrations whose remaining work can ship independently.

## Common exit reasons

- Insufficient evidence, reviewer rejection, or exhausted CI/merge fallback:
  the node is blocked or needs decision.
- `pass-with-nits` normally returns to the worker for suggestions without using
  a review bounce or entering needs-decision.
- Final acceptance still has failures after `acceptance.max_rounds`: the report
  retains the failure list.

## Failure isolation

- Hard-dependent downstream nodes become blocked and are not dispatched.
- Independent branches continue; one failure does not stop all work.
- The Controller Agent may reassign, split into two or three smaller nodes,
  reduce scope, or accept partial failure.

```yaml
# A repeatedly failing node
nodes:
  jwt-service:
    worker: frontend-agent
    blocked_by: [oauth-setup]

# Reassign and split along independently verifiable boundaries
nodes:
  jwt-core:
    worker: backend-agent
    blocked_by: [oauth-setup]
  jwt-middleware:
    worker: frontend-agent
    blocked_by: [jwt-core]
```

## Completion conditions

- Every exit 20 node has an explicit decision and reason.
- A changed manifest passes `omac dag check`.
- `omac dag run` was started again, or a Human received a clear explanation for
  why it is not being resumed.
- Before reporting completion, inspect the manifest. Non-terminal nodes without
  an active `dag run` mean the workflow is not complete.

## Prohibitions

- Do not retry automatically.
- Do not bypass failure isolation to advance a blocked node.
- Do not accept or abandon from guesses before reading instance facts and evidence.
- Do not report exit 20 as success.

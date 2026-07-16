# Building the v1 foundation on Multica

The first useful case study for oh-my-multica is its own early engineering
history. Between July 4 and July 9, 2026, a Multica project coordinated the work
that turned the repository into the v1 foundation: the deterministic Loop,
planning pipeline, task contracts, evidence gates, review handoffs, CI and merge
closure, final acceptance, and the local web view.

There is an important boundary to state plainly. The finished oh-my-multica
controller did not travel back in time and orchestrate its entire creation. This
case records the Multica-based collaboration that produced the foundation and
the problems that led to the delivery control layer. Public Pull Requests verify
the code changes; a sanitized summary of the Multica project records the run
history.

![The v1 foundation delivery record](../assets/v1-foundation-delivery-record.svg)

## The delivery record

| Fact | Observed result |
| --- | ---: |
| Execution window | 4 days, 20 hours, 41 minutes |
| Work items | 29 total, 29 done |
| Delivery stages | 7 |
| Agent runtimes | 3 development, 1 independent reviewer |
| Agent runs | 168 |
| Completed runs | 132 |
| Failed runs | 15 |
| Cancelled runs | 21 |
| Retry attempts | 8 |
| Pull Requests | 27 linked; 26 merged; 1 superseded and closed |

The raw, sanitized numbers are stored in
[`data/v1-foundation-summary.json`](data/v1-foundation-summary.json). The public
GitHub history includes the first CI foundation in
[PR #1](https://github.com/xiaohei-info/oh-my-multica/pull/1), the deterministic
Loop in [PR #6](https://github.com/xiaohei-info/oh-my-multica/pull/6), the
delivery-level end-to-end closure in
[PR #29](https://github.com/xiaohei-info/oh-my-multica/pull/29), and the later
metadata cleanup in [PR #37](https://github.com/xiaohei-info/oh-my-multica/pull/37).

## How the work was split

The project started with one EPIC and a staged delivery plan. Early nodes built
the Loop, project initialization, CI, task metadata, and evidence schemas. The
middle stages added the Agent execution protocol, bounded retries, planning,
live Multica integration, the web view, and Pull Request closure. The final
stages added the acceptance outer loop and end-to-end release preparation.

That order mattered. Work on planning and the web view could proceed in parallel
once the state and task contracts existed. CI fallback could not close before
evidence and work submission were defined. Final acceptance depended on the
merge path. The dependencies were engineering facts, not a preference for how
many Agents to run at once.

Three development runtimes performed implementation work. A separate reviewer
runtime handled independent review. The reviewer accounted for 51 of the 168
runs, which is a useful reminder: review is not a decorative final prompt. It is
a substantial part of the workload and budget.

## The runs did not follow a clean success path

Fifteen runs failed, and 11 work items recorded at least one error. Eight runs
were explicit retries. Twenty-one runs were cancelled; cancellation in this
dataset includes superseded work and interrupted attempts, so it should not be
read as another failure count.

One release-preparation item is representative. Its first attempt stopped after
a semantic-inactivity timeout. A later attempt also timed out after doing useful
work. The continuation retained the task state, fixed two Loop defects, completed
the end-to-end verification, and produced
[PR #29](https://github.com/xiaohei-info/oh-my-multica/pull/29). The independent
review then passed it with minor notes.

This is the part that would disappear in a polished toy demo. Long-running Agent
work fails in ordinary ways: context stalls, commands time out, a first design
misses a state transition, or one branch is replaced by a better implementation.
The system needs persistent facts and an explicit way to resume. A bigger prompt
does not provide either one.

## What Multica handled, and what was still missing

| Multica provided | The missing delivery control problem |
| --- | --- |
| Shared projects and work items | How a requirement becomes reviewed design and acceptance criteria |
| Agent runtimes on multiple machines | Which tasks are genuinely ready and safe to run in parallel |
| Assignment, run history, comments, and state | What structured evidence is sufficient to advance |
| Reusable Agent and Skill configuration | Who reviews independently and when rework stops |
| Persistent collaboration facts | When merged code has passed final user-flow acceptance |

oh-my-multica was built to occupy that second column. Agents still do the work
that needs judgment: design, decomposition, implementation, review, and
acceptance. Deterministic software owns dependencies, result collection, evidence
gates, bounded rework, recovery, merge conditions, and completion.

## What the numbers do and do not prove

The case proves that the project used a real multi-Agent delivery process with
parallel implementation, independent review, failed runs, retries, and public
code integration. It does not prove a universal speedup over a human team, and
it does not claim that every Agent result was production-ready on first attempt.

The Multica usage export records roughly 89.0 million input tokens, 7.2 million
output tokens, and 727.3 million cache-read tokens. Those values show the scale
of the run history, but they are not a portable cost benchmark. Provider
accounting, caching, context reuse, and model pricing differ too much for a
meaningful dollar comparison without a controlled experiment.

The more practical result is simpler: 29 bounded work items reached done, 26
Pull Requests merged, failed attempts remained visible, and the work could
continue after interruptions without reconstructing the project from chat
memory.

## Try the mechanism without a live workspace

The repository includes a small mock-engine demonstration that intentionally
fails one DAG node, returns exit 20, retries the same node, and then converges.
It is not a substitute for the case above, but it is a quick way to inspect the
control flow locally. See [`docs/demo/`](../demo/README.md).

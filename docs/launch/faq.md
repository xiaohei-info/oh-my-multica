# Launch FAQ

## Is oh-my-multica another Coding Agent?

No. Codex, Claude Code, and other Coding Agents do the implementation work.
Multica manages their shared workspace, work items, runtimes, and run history.
oh-my-multica is the delivery control layer above those components.

## Why is it tied to Multica?

Multica already provides the open-source runtime and collaboration substrate the
project needs. Rebuilding machines, runtimes, task queues, work-item state, and
Skill management would add little value. oh-my-multica focuses on the missing
software-engineering path from requirement to final acceptance.

## How is this different from launching several Agent sessions?

Parallel sessions increase execution capacity. They do not automatically define
dependencies, evidence requirements, independent review, bounded rework,
recovery, merge conditions, or project-level completion. oh-my-multica makes
those facts part of the process.

## Does it guarantee production-ready code?

No system can make an incorrect requirement or weak test suite safe. The promise
is narrower: requirements, contracts, verification, review, merge, and final
acceptance become explicit gates instead of remaining in chat history or Human
memory.

## Is there a real end-to-end example?

Yes. The public
[Webhook Inbox demo](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox)
was delivered from one goal through a dynamically planned five-node DAG and five
merged Pull Requests. It passed 86 tests with 97.18% coverage and 11/11 final
acceptance flows. The first acceptance round passed only 2/11 because of a stale
entry point; the Loop refused completion until the source was corrected and the
full document passed.

## Why can cheaper models perform development nodes?

They are given bounded contracts and are checked by deterministic commands,
structured evidence, independent review, and final acceptance. A cheaper model
can still fail, and failed work returns to rework. The system does not treat a
lower price as proof of quality.

## Is the outer Loop Agent-driven?

No. Agents author the design and dynamic DAG and execute individual nodes. Once
the plan passes its gates, deterministic software owns scheduling, state,
evidence checks, retry bounds, recovery, and stop conditions.

## What are the prerequisites?

Python 3.10 or later and a Git repository with at least one commit and a
pushable remote. Real multi-Agent execution also requires Multica and connected
Coding Agent runtimes.

## What does exit 20 mean?

The Loop cannot continue safely without a caller decision. It returns a
structured report and exact next actions instead of guessing about scope, risk,
or recovery.

## How much does it cost?

There is no universal figure. Machine count affects parallelism, while model
choice and Token budget affect reasoning, implementation, retesting, and
rework. Provider caching and accounting differ, so the project does not publish
an unsupported dollar benchmark.

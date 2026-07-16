# Show HN launch

Rules checked on July 16, 2026:
https://news.ycombinator.com/showhn.html

## Submission title

Show HN: oh-my-multica – Deterministic delivery loops for Coding Agent teams

## Link

https://github.com/xiaohei-info/oh-my-multica

## First comment

I built this after spending a lot of time running several Coding Agents in
parallel and noticing that code generation was no longer the main problem.

The difficult part was deciding whether the whole change was actually done.
Requirements drifted between sessions, Agent summaries were treated as test
evidence, review depended on the author’s explanation, and a long-running task
could time out after useful work without leaving a reliable continuation point.

oh-my-multica is a delivery control layer built on the open-source Multica
runtime platform. Multica gives it shared work items, task queues, connected
Coding Agent runtimes, and persistent run history. The added layer works like
this:

1. Planner and Orchestrator Agents inspect the repository and write the design,
   acceptance criteria, project rules, and a dependency DAG.
2. Schemas, lint, and independent review gate those artifacts.
3. Deterministic software takes over the outer Loop: collect results, verify
   evidence, compute ready nodes, dispatch bounded tasks, rework failures, merge,
   and run final acceptance.
4. The Loop returns exit 0 only after convergence. Decisions outside the
   configured boundary return exit 20 with structured next actions.

The distinction I care about is who owns the loop. Models keep freedom where
reasoning is useful, but a supervising Agent does not improvise dependencies,
retry limits, evidence requirements, or the final completion decision from its
current context.

The repository includes three ways to inspect it without trusting the pitch:

- A real Webhook Inbox delivered from one goal through a dynamically planned
  five-node DAG and five merged Pull Requests. It finished with 86 tests,
  97.18% coverage, and 11/11 final-acceptance flows. The first acceptance round
  passed only 2/11 because of a stale application entry point; the Loop refused
  completion until the source was fixed and the full document reran.
- A sanitized case study from the Multica project that built the early v1 foundation:
  29 completed work items, 168 Agent runs, 15 failed runs, 8 retries, and 27
  linked Pull Requests.
- A local mock demo that deliberately fails one node, returns exit 20, retries
  the same DAG, and converges. It requires no account, model, or email.

The main prerequisite for real use is a Git repository with a pushable remote,
plus Multica and connected Coding Agent runtimes. More machines increase
execution concurrency; the model mix is up to you. I use stronger models for
design and quality judgment, and cheaper models for the many bounded
implementation and testing nodes.

I’m looking for blunt feedback on setup friction, unsafe assumptions, and which
repository shapes fail. I’ll be around to answer technical questions.

## Posting checklist

- Confirm `pipx install oh-my-multica` works from a clean machine.
- Confirm the GitHub Release and local demo are public.
- Use the repository as the submission URL, not the case-study article.
- Be available for several hours after posting.
- Do not ask anyone to upvote or seed comments.

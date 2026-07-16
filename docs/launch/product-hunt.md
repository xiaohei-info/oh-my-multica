# Product Hunt launch

Account and posting requirements checked on July 16, 2026:
https://help.producthunt.com/en/articles/771527-personal-account-vs-company-account

## Product fields

**Name**

oh-my-multica

**Tagline**

Production-grade delivery loops for Coding Agent teams

**Short description**

oh-my-multica turns a software requirement into a reviewed, merged, and finally
accepted change. Agents dynamically plan the design and delivery DAG; a
deterministic Loop controls dependencies, evidence, review, bounded rework,
recovery, and completion on top of Multica.

**Links**

- Website: https://github.com/xiaohei-info/oh-my-multica
- Documentation: https://github.com/xiaohei-info/oh-my-multica#readme
- Demo: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/demo/README.md
- Case study: https://github.com/xiaohei-info/oh-my-multica/blob/main/docs/case-studies/building-v1-on-multica.md

**Topics**

- AI Coding Agents
- AI Agents
- Engineering & Development
- Developer Tools
- Open Source

## Maker comment

I built oh-my-multica because adding more Coding Agents increased throughput,
but it did not answer the uncomfortable question: when is the complete software
change actually done?

The project sits above Multica. Multica manages work items, runtimes, task
queues, and persistent execution history. oh-my-multica adds reviewed design and
acceptance, a dynamically planned delivery DAG, structured verification
evidence, independent review, bounded rework, merge control, recovery, and final
user-flow acceptance.

I deliberately kept the outer Loop deterministic. Agents decide how to design,
decompose, implement, review, and test within each boundary. Software decides
which node is ready, whether evidence is valid, whether rework remains within
budget, and whether the entire delivery has converged.

The repository includes a real build record rather than a selected success
clip. The v1 foundation involved 29 completed work items, 168 Agent runs, 15
failed runs, 8 retries, and 27 linked Pull Requests. It also includes a local
mock demo that anyone can run without an account or model Tokens.

This is the first public release, and I’m most interested in practical feedback:
installation failures, unclear concepts, repository assumptions that do not
hold, and real delivery cases where the Loop either helps or gets in the way.

## Gallery order

1. `docs/assets/oh-my-multica-social-preview.png`
2. `docs/diagrams/oh-my-multica-harness-engineering.svg`
3. `docs/diagrams/oh-my-multica-overall-architecture.svg`
4. `docs/assets/v1-foundation-delivery-record.svg`
5. `docs/assets/oh-my-multica-demo.svg`

## Readiness notes

- Use a personal Maker account with a real name and profile photo. Product Hunt
  currently restricts posting and interaction from company accounts.
- The personal account must normally be at least one week old before launch.
- Do not schedule Launch Day until the GitHub Release, PyPI install, and first
  external-user fixes are complete.
- Do not ask for upvotes. Ask users to try the demo and leave concrete feedback.

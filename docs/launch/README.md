# oh-my-multica launch control

This directory contains the copy, evidence, assets, and checkpoints for the
first public release. It is operational material, not another product design.

## Current state — July 16, 2026

| Item | State |
| --- | --- |
| GitHub community files and Discussions | Enabled; welcome post published |
| Private vulnerability reporting | Enabled |
| PyPI Trusted Publishing | Deferred; not required for the current GitHub distribution |
| GitHub Release v1.0.0 | Published with verified wheel and source distribution |
| Release installation | GitHub Tag build passed install tests on Python 3.10 and 3.12 |
| Real Multica case study | Ready in English and Simplified Chinese |
| Real end-to-end Webhook Inbox demo | Public; 5/5 DAG nodes, 5 merged PRs, 11/11 final acceptance |
| Local failure-and-recovery demo | Ready; script, asciinema cast, and animated SVG |
| External posts | Drafted, not published |

## Recommended order

1. Use the immutable `v1.0.0` GitHub Tag and Release as the stable distribution.
2. Soft-launch to Multica Show and tell and Discord.
3. Fix installation or documentation blockers found by the first users.
4. Publish Show HN and the Chinese community posts on separate days.
5. Schedule Product Hunt after the Maker account is eligible and the first
   external feedback is reflected in the product page.

PyPI remains deferred. Browser-account posts and any later PyPI publication
remain explicit Human actions; GitHub-hosted materials can be revised safely.

## Materials

- [`github-discussions.md`](github-discussions.md)
- [`multica.md`](multica.md)
- [`show-hn.md`](show-hn.md)
- [`chinese-communities.zh-CN.md`](chinese-communities.zh-CN.md)
- [`product-hunt.md`](product-hunt.md)
- [`faq.md`](faq.md)
- [`metrics/`](metrics/README.md)

The launch is grounded in three inspectable records: the
[real end-to-end Webhook Inbox delivery](../case-studies/webhook-inbox-end-to-end.md), the
[early v1 Multica delivery record](../case-studies/building-v1-on-multica.md), and the
[reproducible local mock demo](../demo/README.md).

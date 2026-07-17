# oh-my-multica launch control

This directory contains the copy, evidence, assets, and checkpoints for the
first public release. It is operational material, not another product design.

## Current state — July 17, 2026

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
| GitHub-hosted community posts | Published in this repository and Multica Show and tell |
| Browser-account posts | Discord, Show HN, Chinese communities, and Product Hunt remain drafted |

## Recommended order

1. Monitor the GitHub and Multica discussions for concrete setup feedback.
2. Fix installation or documentation blockers found by the first users.
3. Publish Discord, Show HN, and the Chinese community posts on separate days
   when the required browser accounts are available.
4. Schedule Product Hunt after the Maker account is eligible and the first
   external feedback is reflected in the product page.

PyPI remains deferred. Browser-account posts and any later PyPI publication
remain explicit Human actions; GitHub-hosted materials can be revised safely.

## Published links

- GitHub Release: https://github.com/xiaohei-info/oh-my-multica/releases/tag/v1.0.0
- Welcome discussion: https://github.com/xiaohei-info/oh-my-multica/discussions/62
- Webhook Inbox Show and tell: https://github.com/xiaohei-info/oh-my-multica/discussions/64
- Multica Show and tell: https://github.com/multica-ai/multica/discussions/5545
- Real demo Release: https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox/releases/tag/v1.0.0

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

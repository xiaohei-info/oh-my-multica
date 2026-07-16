# oh-my-multica launch control

This directory contains the copy, evidence, assets, and checkpoints for the
first public release. It is operational material, not another product design.

## Current state — July 16, 2026

| Item | State |
| --- | --- |
| GitHub community files and Discussions | Ready and enabled |
| Private vulnerability reporting | Enabled |
| PyPI Trusted Publishing workflow | Ready; `pypi` environment requires Human approval |
| Release Candidate | Built and install-tested on Python 3.10 and 3.12 |
| Real Multica case study | Ready in English and Simplified Chinese |
| Real end-to-end Webhook Inbox demo | Public; 5/5 DAG nodes, 5 merged PRs, 11/11 final acceptance |
| Local failure-and-recovery demo | Ready; script, asciinema cast, and animated SVG |
| Git Tag / PyPI / GitHub Release | Not published |
| External posts | Drafted, not published |

## Recommended order

1. Configure the pending PyPI Trusted Publisher for package
   `oh-my-multica`, workflow `release.yml`, environment `pypi`.
2. Approve and push the immutable `v1.0.0` Tag.
3. Approve the `pypi` deployment; verify the package and GitHub Release.
4. Publish the GitHub Discussions welcome post.
5. Soft-launch to Multica Show and tell and Discord.
6. Fix installation or documentation blockers found by the first users.
7. Publish Show HN and the Chinese community posts on separate days.
8. Schedule Product Hunt after the Maker account is eligible and the first
   external feedback is reflected in the product page.

Tag creation, PyPI approval, GitHub Release publication, and every external
post remain explicit Human actions. All other preparation can be revised safely.

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

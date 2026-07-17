# Launch metrics

oh-my-multica does not add anonymous product telemetry. Launch measurements use
public platform data and feedback that users choose to submit.

Collect a snapshot with an authenticated GitHub CLI session:

```bash
python3 scripts/collect_launch_metrics.py \
  --output docs/launch/metrics/<date>-<checkpoint>.json
```

Recommended checkpoints are prelaunch, 24 hours, 72 hours, 7 days, and 30 days.
The committed prelaunch baseline is
[`2026-07-16-prelaunch.json`](2026-07-16-prelaunch.json). The first checkpoint
after the GitHub Release and GitHub-hosted community posts is
[`2026-07-17-github-launch.json`](2026-07-17-github-launch.json).

## How to read the numbers

- Stars and Forks are interest signals, not proof that a delivery succeeded.
- GitHub traffic covers a rolling window and should be compared with the
  prelaunch baseline.
- Clone counts are heavily inflated by this project's own Agent, CI, and
  worktree activity. Use post-launch deltas, not the absolute total, and do not
  present clones as unique users.
- Release-asset downloads exclude installs directly from a GitHub Tag and any
  future PyPI installs.
- PyPI Stats may lag a new package or rate-limit requests; an unavailable value
  should remain unavailable rather than being guessed.
- The strongest activation signal is still voluntary: a user reports that they
  installed the project, created a plan, or converged a real DAG.

## Feedback review

At each checkpoint, record:

1. Installation blockers and the platform where they occurred.
2. The first concept users misunderstood.
3. Real exit 20 reasons that were useful or confusing.
4. New Discussions, Issues, Pull Requests, and delivery stories.
5. Source channels that produced actionable feedback, not just clicks.

Use the results to update documentation and onboarding first. Do not add product
features solely because a launch post received a high-engagement comment.

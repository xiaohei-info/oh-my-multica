# GitHub-side setup (done by a maintainer, not by code)

The workflows in `.github/workflows` are verified locally as far as they can be (see "What was checked"). Everything below
lives in the repository or organisation settings and has **not** been done or checked from the code base. Tick each item
when it is done.

## Required before the first release

- [ ] **Actions are allowed** for this repository, and the action versions pinned in the workflows exist
      (`actions/checkout@v7`, `actions/setup-python@v6`, `actions/upload-artifact@v7`, `actions/download-artifact@v8`,
      `pypa/gh-action-pypi-publish@release/v1`). They could not be looked up offline.
- [ ] **Branch protection on `main`** requires these checks: `unit and integration tests (Python 3.10)`,
      `unit and integration tests (Python 3.12)`, `Docker tests, shard 1 of 3`, `Docker tests, shard 2 of 3`,
      `Docker tests, shard 3 of 3`. The three shards together are the whole `tests/it` directory; adding a test file needs
      no workflow change, but changing the shard count in `ci.yml` means changing the `--shard I/N` argument and the matrix
      together.
- [ ] **Fork pull requests need approval**: Settings -> Actions -> General -> "Fork pull request workflows" -> *Require
      approval for all outside collaborators*. `ci.yml` runs Docker and downloads a release binary; it should not start
      for a stranger's pull request without a look.
- [ ] **PyPI trusted publishing** is configured for `oh-my-multica` with the workflow `release.yml` and the environment
      `pypi` (the environment exists in the repository settings).

## Required before the first Smoke run (a real Coding Agent; it costs money)

- [ ] **A dedicated self-hosted runner** with the labels `self-hosted`, `macOS`, `omac-smoke`, Docker, the `multica` CLI that
      matches `tests/it/compose.yml`, and a signed-in `claude` CLI. Nothing else should share that machine's Docker.
- [ ] **An environment named `smoke`**, restricted to the `main` branch, holding the secret `OMAC_SMOKE_ANTHROPIC_API_KEY`
      (a *metered* key, not a subscription login).
- [ ] **A hard monthly limit of 20 USD on that key in the provider console.** The spend ledger
      (`tests/smoke/ledger.py`, cap `OMAC_SMOKE_MONTHLY_CAP_USD` = 20) is the second layer, not the first.
- [ ] **Self-hosted runners are not reachable from fork pull requests**: the Smoke workflow has no `pull_request` trigger
      (`tests/test_architecture.py` enforces that). Also check the repository setting for fork workflows above, and do the
      experiment once: open a pull request from a fork that edits `smoke.yml` and confirm nothing starts without approval.

## What was checked locally (2026-10-26)

- Every workflow parses; every `needs` reference exists; every step has `uses` or `run`; `smoke.yml` has only `schedule` and
  `workflow_dispatch` triggers.
- The commands of `ci.yml` run: `lint-imports`, `pytest tests/ -m "not docker and not live"` and each `--shard I/3` of
  `tests/it` (the shards cover all 789 Docker tests exactly once; `tests/test_shard.py`).
- The commands of `release.yml` run on a clean clone: `scripts/check_release.py` accepts `v2.0.0` and refuses `v9.9.9` and
  `2.0.0`; `python -m build` produces the sdist and the wheel; the sdist holds no `tests/fixtures`; the wheel installed in a
  fresh virtual environment reports `omac 2.0.0` and its `--help` contains `oh-my-multica`.
- Not run: `act` and `actionlint` are not installed and nothing may be installed here; `twine check --strict` (not
  installed); anything that needs GitHub itself (runners, secrets, environments, PyPI, `gh release create`).

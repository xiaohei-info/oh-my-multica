# GitHub-side setup (done by a maintainer, not by code)

These live in the repository or organisation settings. Tick each item when it is done.

- [ ] **Actions are allowed** for this repository, and the action versions pinned in the workflows exist
      (`actions/checkout@v7`, `actions/setup-python@v6`, `actions/upload-artifact@v7`, `actions/download-artifact@v8`,
      `pypa/gh-action-pypi-publish@release/v1`).
- [ ] **Branch protection on `main`** requires these checks: `unit and integration tests (Python 3.10)`,
      `unit and integration tests (Python 3.12)`, `Docker tests, shard 1 of 3`, `Docker tests, shard 2 of 3`,
      `Docker tests, shard 3 of 3`. Changing the shard count in `ci.yml` means changing the `--shard I/N` argument, the
      matrix and this list together.
- [ ] **Fork pull requests need approval**: Settings -> Actions -> General -> "Fork pull request workflows" -> *Require
      approval for all outside collaborators*. `ci.yml` runs Docker, and the smoke runner is self-hosted.
- [ ] **PyPI trusted publishing** is configured for `oh-my-multica` with the workflow `release.yml` and the environment
      `pypi` (the environment exists in the repository settings).
- [ ] **Optional, for the manual smoke run**: the self-hosted runner is registered with the labels `self-hosted`, `Linux`,
      `taiyi`. That machine has Docker, the `multica` CLI that matches `tests/it/compose.yml`, and Codex and Pi signed in;
      no secret or environment is needed. Start it with `gh workflow run smoke.yml -f runtime=codex` (or `pi`).

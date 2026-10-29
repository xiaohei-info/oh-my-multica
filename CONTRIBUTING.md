# Contributing to oh-my-multica

Contributions are welcome, whether they improve the delivery engine, clarify
the documentation, or make the first run easier to understand.

## Before opening a change

- Use GitHub Discussions for usage questions and early design ideas.
- Search existing Issues before reporting a bug or proposing a feature.
- Keep changes focused. Separate unrelated refactoring from the behavior or
  documentation you intend to improve.
- Preserve the public CLI, JSON and exit-code contracts, and the three engine ports (`WorkItemStore`, `AgentRuntime`,
  `CodeHost` in `src/omac/engines/`). New platform behavior goes into an adapter behind a semantic port method.
- Only generic mechanisms belong in `src/omac`. Recovery for one incident is operator tooling written outside the
  package, reviewed, run and deleted (see `AGENTS.md`, rule 4).

## Local setup

```bash
git clone https://github.com/xiaohei-info/oh-my-multica.git
cd oh-my-multica
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]" import-linter
```

Run focused checks while developing. Before opening a Pull Request, verify the
change in proportion to what it touches:

- Source code, tests, package/build configuration, schemas, or executable
  behavior: `python -m pytest tests/ -m "not live" -n 4 --dist loadfile` (needs Docker; add `-m "not docker and not live"` without it) and `lint-imports`
- Markdown-only changes: `git diff --check`, local-link checks, and rendering
  or parsing for changed diagrams or formats when available.

Unit tests stay small and fast because the code keeps decisions pure and platform calls behind ports; there is no
per-test time or memory budget to satisfy.

`tests/it` runs against a private Multica started with Docker (compose project `omac-it-*`, loopback port, removed
afterwards) and needs the `multica` CLI on `PATH`; it fails with the fix when Docker is missing instead of skipping.
Run `python tests/it/cleanup.py` to remove stacks left behind by a killed run. The weekly real-agent smoke run
(`tests/smoke`, `.github/workflows/smoke.yml`) costs money, never runs for pull requests, and is capped at 20 USD per
month. Tests marked `live` need a real platform and are skipped by default.
Do not include credentials, machine paths, workspace IDs, Agent IDs, or user
email addresses in tests, fixtures, screenshots, or logs. The design is in `docs/design/README.md`.

## Pull requests

A useful Pull Request makes four things clear:

1. The user-visible problem.
2. The smallest solution that addresses it.
3. Compatibility and operational risks.
4. Objective verification evidence.

Develop new or changed behavior test-first, covering the main path, important
boundaries, and known failure modes. Update the documentation when commands,
behavior, or user expectations change.

By contributing, you agree that your contribution is licensed under the MIT
License and that you will follow the repository Code of Conduct.

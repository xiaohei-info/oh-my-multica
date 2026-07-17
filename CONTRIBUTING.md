# Contributing to oh-my-multica

Contributions are welcome, whether they improve the delivery engine, clarify
the documentation, or make the first run easier to understand.

## Before opening a change

- Use GitHub Discussions for usage questions and early design ideas.
- Search existing Issues before reporting a bug or proposing a feature.
- Keep changes focused. Separate unrelated refactoring from the behavior or
  documentation you intend to improve.
- Preserve the public CLI, JSON, exit-code, and engine-interface contracts.

## Local setup

```bash
git clone https://github.com/xiaohei-info/oh-my-multica.git
cd oh-my-multica
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest
```

Run focused checks while developing. Before opening a Pull Request, verify the
change in proportion to what it touches:

- Source code, tests, package/build configuration, schemas, or executable
  behavior: `python -m pytest tests/`
- Markdown-only changes: `git diff --check`, local-link checks, and rendering
  or parsing for changed diagrams or formats when available.

Live tests require a configured Multica workspace and are skipped by default.
Do not include credentials, machine paths, workspace IDs, Agent IDs, or user
email addresses in tests, fixtures, screenshots, or logs.

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

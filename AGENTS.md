# AGENTS.md — Repository Development Conventions

This file contains implementation guardrails specific to the `oh-my-multica`
(`omac` CLI) repository. Cross-project engineering practices are intentionally
omitted; only repository-specific, non-negotiable constraints belong here.
Rules marked as enforced are checked by CI (`lint-imports`, `tests/test_architecture.py`);
the rest are review requirements.

## Repository Map

| Path | Role |
|---|---|
| `src/omac/web/` | Read-only web surface. |
| `src/omac/cli/` | CLI surface and composition root (argument parsing, engine assembly, exit codes). |
| `src/omac/pipeline/` | Deterministic Loop: reconcile, result collection, gates, dispatch, delivery, acceptance. |
| `src/omac/engines/` | Ports (`store.py`, `runtime.py`, `codehost.py`, `models.py`) and adapters (`multica.py`, `github.py`); `create_engine` is the only factory. |
| `src/omac/core/` | Domain model, manifest I/O, validation, Git state sync. |
| `src/omac/guide/`, `src/omac/agents/` | Packaged, machine-consumed guides and Agent templates. |
| `scripts/check_release.py` | Release metadata check used by `release.yml`. |
| `tests/` | pytest suite; markers `integration`, `docker`, `live` (see `pyproject.toml`). `tests/it` runs a private Docker Multica; `tests/smoke` is the weekly real-agent run. |
| `docs/design/README.md` | The current design; code comments cite its section numbers. |

## Hard Rules

### 1. Platform boundary

`multica`, `gh`, and any other platform CLI are executed only inside engine
adapters (`src/omac/engines/multica.py`, `github.py`). Code in `pipeline`, `core`,
`cli`, and `web` must not execute them, and must not depend on their command
syntax or on Agent runtime transcript formats (for example, by matching
`["multica", "issue", ...]` in session logs). If a fact is needed, add a semantic
method to the port and let the adapter produce structured data.

Local tooling is allowed only where it already exists: `git` (Git state sync and
repository reads), `bash -n` (syntax preflight), and `lsof`. Do not add new
subprocess call sites outside adapters without updating the allowlist in
`tests/test_architecture.py`. (enforced)

This is a code-review hard stop.

### 2. Layering

Dependency direction is strictly downward:

`web → cli → pipeline → engines → core → i18n → errors`

- Function-level (lazy) imports count. A lazy import is never a way around a
  layering problem; move the code to the right layer instead.
- Concrete adapters (`engines.multica`, `engines.github`) are imported only by
  `engines.create_engine`. The CLI assembles engines through one helper; do not
  add another assembly path.
- The only permanent exception is the `omac web` command starting
  `omac.web.server`.
- Enforced by `lint-imports` (contracts in `pyproject.toml`). The baseline
  exception list may only shrink. (enforced)

### 3. Web surface

The web routing layer may only parse parameters, call command functions, and
return JSON unchanged. It must not read manifests, call engines, transform data,
or swallow exceptions. Humans, agents, and the web interface must always see the
same facts. When the web needs data that no command returns, add a CLI command
with `--output json` first.

### 4. No incident-specific code in the product

`src/omac` must not contain captured identifiers (work item, workspace, or run
UUIDs; commit or file SHA digests; specific node names; URLs) used as admission
conditions or special cases. One-off recovery for a single incident is written
as separate operator tooling outside the package, reviewed, run, and deleted.
Generic mechanisms (unknown results are never replayed, drift fails closed) stay
in the product without incident constants. (enforced)

### 5. Manifest state

- One writer per manifest, guarded by the manifest write lock
  (`core/manifest.py`). The Multica engine synchronizes the DAG definition, the
  per-node state files and the config through `origin/main`.
- Run state is one small file per node (`.omac/<name>.state/<node>.yaml`);
  `save_manifest` rewrites only files whose content changed. Do not add a code
  path that rewrites the whole state or reads a 1.x single-file manifest.
- `meta` keys that the current code does not know are preserved unchanged on
  load and save.
- Do not store unbounded or large payloads (captured sources, transcripts,
  archives) in the manifest. Store an attachment reference and a digest instead.

## Exit-Code Contract (Stable and Safe for Script Branching)

| Code | Meaning |
|---|---|
| `0` | The DAG converged (no running, blocked, or failed nodes; `done`, `abandoned`, and `cancelled` count as converged) and, when acceptance is required, final acceptance passed. |
| `1` | General error. |
| `2` | Platform or network error (including a missing work item). |
| `3` | Authentication error, such as a platform CLI that is not signed in. |
| `5` | Validation failure, such as lint or the evidence schema. |
| `10` | Still advancing: returned by `dag tick`, and by `dag run` when `--max-rounds` or `--max-minutes` is reached. Rerun the same command to continue. |
| `20` | Caller decision required; includes a structured report. |

Codes are defined in `src/omac/cli/exit_codes.py`. Business-layer `OmacError`
subclasses (`src/omac/errors.py`) map to their exit codes. `sys.exit` appears
only in `src/omac/cli/main.py`. The contract must remain backward compatible.

## Terminology (Required in User-Facing Output and Documentation)

| Required term | Chinese | Do not use |
|---|---|---|
| result collection (`collect_results`) | 结果收集 | harvest |
| running nodes (`running_nodes`) | 进行中节点 | in-flight, 在飞 |
| ready nodes (`ready_nodes`) | 就绪节点 | frontier |

Use the required terms in identifiers, error messages, guide text, and design
documents. Avoid ad hoc translated jargon.

## Errors Teach Users

User-facing output and errors must state what is missing, how to fix it, and a
copyable command. For invalid arguments, print the error and the command's full
help text so an agent can correct itself after one mistake. All user-facing text
goes through `omac.i18n.ui(english, chinese)`.

## Tests

1. **Write tests before implementation.** New or changed executable behavior
   needs regression coverage of the main path, boundaries, and known risks.
2. **CI-equivalent run:** `python3 -m pytest tests/ -m "not live"`. Code must
   work on Python 3.10 and 3.12. `live` tests need a configured Multica
   workspace and run only on request (`-m live`).
3. **Tests protect behavior, not text.** Do not assert on source code text or
   `inspect.getsource`, do not test the test doubles themselves, do not restate
   constants (`assert X == set(X_MAP)`), and mock only external boundaries
   (platform CLI, network, clock). Duplicate test names are a defect.
4. **Fixtures are synthetic and small.** No captured real-project data, machine
   paths, platform IDs, emails, or credentials (see `CONTRIBUTING.md`). A single
   fixture file stays under 1 MB. Tests must not read paths outside the
   repository. (enforced)
5. **Architecture checks** live in `tests/test_architecture.py` and
   `lint-imports`; do not weaken them to make a change pass. (enforced)
6. Per-test budgets are enforced by `tests/conftest.py` (`tests/_support/budget.py`):
   unit 0.2 s target (0.4 s gate), integration 5 s, docker 30 s, peak memory and
   temp-file limits. Do not add files to `EXEMPT_FILES` without a reason in the PR.
7. Tests that need the real server go to `tests/it` (private Docker Multica; never
   skipped silently). The real-agent smoke run costs money and never runs for pull
   requests.
8. Changes to packaged documents (`src/omac/guide/**`, `src/omac/agents/**`)
   run `tests/test_guide.py`, `tests/test_i18n.py`, and
   `tests/test_agent_templates.py`.

## Verification

- Source code, tests, build/package configuration, schemas, or executable
  behavior: the CI-equivalent pytest run must be green, and `lint-imports` must
  pass.
- Documentation-only changes (Markdown or static diagram assets): run
  `git diff --check`, verify local links and assets, and parse or render changed
  formats when tooling is available, plus the packaged-document tests above when
  they apply.
- Writing code is not completion. Completion requires objective evidence from
  tests, builds, or actual command output.

## Design Documents

The current design lives in `docs/design/README.md` (tracked); code comments cite its
section numbers. `docs/dev/` is local and ignored by Git; never cite it from code or
this file. Cite design sections only by documents that exist in the repository.

## Releases

The version comes from `pyproject.toml`. Tags use `v*.*.*`. `release.yml` runs
`scripts/check_release.py`, builds, and checks distributions. Release artifacts
must not contain `tests/fixtures`.

## Definition of Done

A change is complete only when all of the following are true:

- [ ] It stays within the planned scope.
- [ ] It respects the platform boundary and layering rules; no new lazy import
  crosses a layer, and the `lint-imports` baseline did not grow.
- [ ] It adds no incident-specific constants or one-off recovery code to
  `src/omac`.
- [ ] Verification is proportional to the change (see Verification).
- [ ] Documentation, guides, and this file are updated when commands, behavior,
  or rules change.

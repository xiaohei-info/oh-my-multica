# Running tests

Markers (see `pyproject.toml`) only select what runs:

- `integration`: real git, a local HTTP server, subprocesses; included by default.
- `docker`: `tests/it`, a private Docker Multica; Docker is required and these tests are never skipped silently.
- `live`: a real platform; skipped unless requested with `-m live`.

Fast checks while iterating on ordinary logic:

```sh
python3 -m pytest tests/ -m 'not integration and not docker and not live'
```

The CI-equivalent run (install with `python3 -m pip install -e '.[test]' import-linter`):

```sh
python3 -m pytest tests/ -m 'not live' -n 4 --dist loadfile   # without Docker: -m 'not docker and not live'
lint-imports
```

`-n 4 --dist loadfile` keeps each file in one worker. See the
[pytest-xdist scheduling documentation](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).
There is no in-memory platform double: anything that needs platform behaviour is a Docker test, and unit tests
replace only the platform CLI call.

Manifest writers use PyYAML's LibYAML accelerator when it is installed, with the Python backend as a fallback.
YAML formatting can differ between backends; the decoded state values are authoritative.

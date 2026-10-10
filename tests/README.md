# Running tests

Use the fast checks while iterating on ordinary logic:

```sh
python3 -m pytest tests/ -m 'not integration and not e2e and not live'
```

Run the affected integration modules when changing their behavior. Tests marked
`integration` exercise complete captured Source, publication boundaries, state
transport or recovery, using isolated local files and mock adapters. Their full
original bodies and safety assertions remain part of the suite.

Before delivery, run the complete default suite:

```sh
python3 -m pytest tests/
```

The default command includes integration tests; no new default exclusions or
environment-based skips are applied. Existing live-test opt-in rules still apply.

For a parallel full run, install the optional test dependencies into a development
environment, then use four local workers and keep each file in one worker:

```sh
python3 -m pip install -e '.[test]'
python3 -m pytest tests/ -n 4 --dist loadfile
```

This selects the same complete suite. Parallel execution is opt-in; runtime
installations do not require these test dependencies. See the
[pytest-xdist scheduling documentation](https://pytest-xdist.readthedocs.io/en/stable/distribution.html).

Manifest and state-envelope writers use PyYAML's installed LibYAML accelerator
when available, with the original Python backend as a fallback. YAML formatting
can differ between backends; decoded state values and embedded original Source
bytes remain authoritative. Backend roundtrip, fallback, corruption rejection,
atomic save and complete-capture regressions cover that boundary.

Mock plan tests explicitly configure zero completion delay in the same config
read by the CLI. Tests that require an active agent explicitly disable completion
instead of relying on a wall-clock race.

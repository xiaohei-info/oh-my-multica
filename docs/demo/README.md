# Local delivery-loop demo

![oh-my-multica failure and recovery demo](../assets/oh-my-multica-demo.svg)

This demo uses the built-in mock engine, so it does not need a Multica account,
remote repository, Coding Agent runtime, or model Tokens. It demonstrates the
control mechanism rather than model quality:

1. A foundation node unlocks two parallel implementation tracks.
2. The API track is deliberately failed.
3. The Loop returns exit 20 with an explicit recovery path.
4. The same node is retried without recreating the DAG.
5. Both tracks converge and unlock integration.

From the repository root, install the current source and run:

```bash
pipx install . --force
./docs/demo/run-demo.sh
```

Set `DEMO_DELAY=0` for a fast non-interactive verification run. The manifest is
[`demo-manifest.yaml`](demo-manifest.yaml).

This is intentionally smaller than a real delivery. Dynamic design and DAG
planning require configured Planner and Orchestrator Agents on Multica; the
mock demo starts from an already-authored manifest so anyone can inspect failure,
recovery, dependency scheduling, and convergence in under a minute.

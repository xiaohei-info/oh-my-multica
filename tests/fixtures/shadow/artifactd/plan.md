# Artifactd shadow plan

Mark-owned human plan for the Artifactd non-UI shadow scenario.

## Scope

- One machine-plane configuration node (`shadow-config`).
- No assignment, PR, merge, deploy, or product mutation against live systems.
- Evidence stays inside the mock engine and the immutable plan snapshot store.

## Unlock

This file is the durable plan input referenced by:

```text
PlanReturn path=/absolute/path/to/plan.md
```

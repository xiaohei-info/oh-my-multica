# Runtime-backed product review tightening notes

Use this reference when a detailed-design document describes a business/control-plane product built on top of an existing runtime and/or reused host Web UI.

## High-value review questions

1. Is the PRD being used only for page/function completeness, or is it silently defining backend truth?
2. Are reused host routes being mistaken for the product's northbound service contract?
3. Are group-chat entry semantics being conflated with orchestration/kanban execution semantics?
4. Is a business blueprint object being collapsed into a runtime workflow object?
5. Are product object names aligned with their runtime mapping, or are they inventing unnecessary jargon?

## Concrete example patterns

### A. Single-agent chat on a reused host app
Bad detailed-design wording:
- "Team Panel sends `/api/chat/start` and `/api/chat/stream`"

Better wording:
- Team Panel builds a business `RunRequest`
- an internal translation layer converts it into a gateway-executable request
- the gateway exposes an internal capability such as `start_single_agent_run(run_request)`
- V1 may internally reuse existing host SSE/chat routes or call `run_conversation()` directly

Reason:
- the reused host route is an implementation detail
- the business/control-plane contract should survive replacement of the host shell

### B. Browser group chat vs runtime orchestration
Bad detailed-design wording:
- "group chat goes to kanban"

Better wording:
- group chat is an entry surface and routing context
- the runtime first respects message/session/thread semantics
- only after routing does the product choose execution mode:
  - single-agent reply
  - orchestrated collaboration

Reason:
- chat topology and execution strategy are separate layers

### C. Dynamic orchestration decomposition
Bad detailed-design wording:
- Team Panel statically expands the whole task graph and merely submits it

Better wording:
- Team Panel emits a collaboration request carrying task goal, context, candidate profiles/roster, and optional default collaboration template
- the gateway creates an orchestrator-owned root task
- orchestrator dynamically decomposes downstream work and assigns worker profiles
- the control plane subscribes to decomposition and worker events for UI playback

Reason:
- avoids rebuilding a scheduler/orchestrator inside the product control plane
- matches generic task-board and Orchestrator semantics better

### D. Blueprint object vs workflow object
Bad detailed-design wording:
- "IndustrySolution equals workflow"

Better wording:
- the solution is a provisioning/application blueprint
- it may optionally bind a default collaboration template, roster, or orchestration policy
- the workflow remains a runtime execution concern

Reason:
- keeps one-click application/provisioning separate from live task execution

### E. Product naming aligned to runtime mapping
Smell:
- product object name sounds more abstract than the actual runtime mapping and creates unnecessary ambiguity

Example:
- if the product object is simply an employee-bound scheduled task mapped to a runtime cron job, a name like `ScheduledJob` is usually clearer than an invented mission metaphor unless the product truly models higher-level mission semantics

## Recommended rewrite checklist

- Rewrite page/API prose into domain object prose first
- Rewrite reused route names into internal gateway capability names
- Separate entry flow, routing flow, and execution flow
- Check whether any blueprint/catalog object was accidentally treated as a workflow engine object
- Prefer product names that make the runtime mapping obvious unless the business domain truly needs a richer abstraction

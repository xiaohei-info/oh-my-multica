# Runtime-backed derivative project calibration

Use this reference when a product is implemented as a downstream project built from an upstream web UI / runtime-console base.

## Canonical distinction

- **Upstream source base**: the repo/codebase whose host shell, SSE engine, route organization, or UI framework is being reused.
- **Downstream implementation carrier**: the real project/repo where the new product is developed and delivered.

Do not blur the two in detailed design.

## Example pattern

A product may say:
- upstream source base: `hermes-webui`
- downstream implementation carrier: `agent-service`
- internal module layers inside the downstream project:
  - `team-panel/`
  - `agent-gateway/`
  - inherited host substrate such as `server.py`, `api/*`, `static/*`

## Wording rules for design docs

Prefer wording like:
- "The implementation target is `agent-service`, which is a secondary-development project based on the Hermes Web UI codebase."
- "Team Panel and Agent Gateway are internal module layers inside Agent Service V1, not separately deployed services."
- "V1 shares one host process (`server.py`); `api/routes.py` expands by addition; runtime-adapter logic is extracted from existing host modules."

Avoid wording like:
- "We will build on Hermes Web UI" with no mention of the actual downstream repo.
- "Gateway" phrased in a way that makes readers assume a separate network service if V1 is same-process.

## Reuse-anchor checklist

Call out which inherited files remain the substrate, for example:
- `server.py`
- `api/routes.py`
- `api/streaming.py`
- `api/config.py`
- `api/profiles.py`
- `api/models.py`
- `api/upload.py`
- `api/workspace.py`
- `static/index.html`
- `static/ui.js`
- `static/messages.js`
- `static/sessions.js`
- `static/panels.js`

Then separately call out which directories are net-new business logic.

## Data-truth calibration

When the inherited host stores sessions in JSON/files but the product needs a real control-plane database:
- state that host JSON/session storage is **not** the control-plane source of truth
- define the dual-track model explicitly:
  - control-plane truth in relational DB
  - host/session mirror retained for runtime/workbench compatibility

## Review prompts

Ask these before freezing the design:
1. Did we name the real target repo/path?
2. Could a reader confuse internal modules with deployed services?
3. Did we name the concrete inherited files that will remain the substrate?
4. Did we separate control-plane truth from host/session storage truth?
5. Did we say whether route growth is additive or a full rewrite?


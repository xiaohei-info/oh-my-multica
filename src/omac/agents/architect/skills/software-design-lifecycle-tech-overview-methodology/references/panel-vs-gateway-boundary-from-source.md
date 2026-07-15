# Panel vs Gateway Boundary from Source

Session-derived evidence for runtime-based product architecture decisions.

## Question answered
For an AI-Team-style product built on top of Hermes, should the product frontend talk directly to Agent Gateway, or should a Team Panel business layer sit upstream of Gateway?

## Verified source evidence

### 1. Hermes Gateway API Server is a runtime-facing surface
Source: `gateway/platforms/api_server.py`

Documented / registered endpoints include:
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `GET /v1/models`
- `GET /v1/capabilities`
- `POST /v1/runs`
- `GET /v1/runs/{run_id}`
- `GET /v1/runs/{run_id}/events`
- `POST /v1/runs/{run_id}/approval`
- `POST /v1/runs/{run_id}/stop`
- `GET/POST/PATCH/DELETE /api/jobs...`

Useful locations:
- top-level endpoint comment: `api_server.py:4-16`
- route registration: `api_server.py:3363-3386`

### 2. Gateway creates the runtime agent server-side
Source: `gateway/platforms/api_server.py`

`/v1/capabilities` reports:
- `runtime.mode = server_agent`
- `tool_execution = server`
- `split_runtime = False`

Useful location:
- `api_server.py:949-957`

This means the API server is not just a dumb proxy. It instantiates a server-side `AIAgent` and executes tools on the API-server host.

### 3. Gateway is configured from runtime/platform config, not from business objects
Source: `gateway/platforms/api_server.py`

`_create_agent()` resolves:
- runtime kwargs from gateway/runtime config
- gateway model
- platform toolsets from `platform_toolsets.api_server`
- fallback model
- reasoning config

Useful locations:
- `_create_agent()` header: `api_server.py:817-845`
- toolsets / model wiring: `api_server.py:846-851`
- `AIAgent(...)` construction: `api_server.py:859-877`

Implication: the current gateway is runtime-oriented. It is not naturally parameterized by product business objects like employee definitions, template bindings, enterprise spaces, or permission policies.

### 4. Gateway accepts runtime conversation/run fields, not AI-Team business entities
Observed request-body fields in `/v1/runs` and chat/responses handling include:
- `input`
- `instructions`
- `previous_response_id`
- `conversation_history`
- `session_id`
- `model`
- `skills` (for cron job create)

Useful locations:
- chat completions request handling: `api_server.py:995-1049`, `1088-1228`
- responses request handling: `api_server.py:2078-2120`
- runs request handling: `api_server.py:2851-2908`
- cron job create fields: `api_server.py:2428-2430`

Not found as first-class gateway concepts during session review:
- employee
- template
- workspace business object
- permission management
- governance/business control objects

Implication: Gateway currently exposes runtime primitives, not AI-Team product semantics.

### 5. Officially recommended `hermes-webui` does NOT prove that product frontends should call Gateway directly
Source: `hermes-webui`

Verified statements:
- README says the WebUI imports Hermes Python modules directly, **not via HTTP**
- architecture doc says the WebUI imports Hermes modules via `sys.path`
- runtime path uses `from run_agent import AIAgent` and `run_conversation(...)`

Useful locations:
- README: `README.md:203-206`
- README three-container note: `README.md:219-222`
- ARCHITECTURE: `ARCHITECTURE.md:113-117`
- ARCHITECTURE concurrency note: `ARCHITECTURE.md:150-151`
- ARCHITECTURE SSE/agent invocation: `ARCHITECTURE.md:226-280`
- direct import in code: `api/streaming.py:36-57`, `api/routes.py:2948-3000`

Implication: hermes-webui is a lightweight in-process UI backend / CLI-parity shell. It is not evidence that an enterprise product frontend should bypass its own business control plane and talk to Gateway directly.

## Recommended architecture judgment

### Recommended boundary
- **Team Panel** = business control plane / BFF / translation layer
- **Agent Gateway** = runtime access surface / runtime control API
- **Agent Runtime** = execution truth layer

### Recommended calling relation
For the product main path:
- `Frontend -> Team Panel -> Agent Gateway -> Agent Runtime`

Important nuance:
- Team Panel and Agent Gateway can still be separate peer subsystems in deployment/ownership terms
- but in the business request path, Team Panel sits upstream of Agent Gateway

### Why this is the better default
Because Gateway already has runtime-native APIs, but does not own product business objects such as:
- employees
- templates / industry solutions
- enterprise spaces
- permission/governance objects
- business task semantics

So Team Panel still has real value:
- own business objects and bindings
- translate employee/team/task semantics into runtime calls
- project runtime events into product-facing states
- hold governance, audit, cost, and management semantics

### What NOT to do
- Do not treat Gateway as the place for business-facing management interfaces just because it already exposes HTTP endpoints.
- Do not make Team Panel re-wrap every Gateway endpoint 1:1.
- Do not infer from hermes-webui that direct frontend-to-gateway is the primary enterprise product pattern.

### Selective wrapping rule
Good pattern:
- Team Panel wraps only where business translation/projection is needed
- Gateway remains the owner of:
  - run submission
  - event streaming
  - approval
  - stop
  - runtime-native jobs/scheduler surfaces

Potential exception:
- an expert/debug console may talk to Gateway more directly later without changing the main product architecture.


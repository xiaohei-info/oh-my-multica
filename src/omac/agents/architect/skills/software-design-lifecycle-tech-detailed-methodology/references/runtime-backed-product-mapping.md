# Runtime-backed product mapping pattern

Use this reference when the system being designed is a **product/control plane on top of an existing runtime and/or web-console base**.

## Core split

Keep two layers explicit:

1. **Business/control-plane truth**
   - enterprise
   - employee
   - team
   - conversation
   - run/task
   - governance/billing/audit objects
2. **Runtime execution truth**
   - profile / session / message / task / run / cron / memory / skill / connector visibility

Do not let the runtime's internal nouns replace the product's business nouns.

## Mandatory mapping questions

For every important business object, answer all five:

1. What is the business object called?
2. What runtime object actually executes it?
3. What is the static mapping rule?
4. What is the dynamic execution path?
5. Is the path existing, extendable, or net-new?

## Recommended mapping worksheet

- `EmployeeInstance -> RuntimeProfile`
  - static mapping: `employee_id -> profile_name -> profile home dir`
  - dynamic usage: target profile selected for single-agent run / worker spawn / cron execution
- `PrivateConversation -> Session`
  - static mapping: conversation record keeps runtime session handle
  - dynamic usage: user message -> single-agent run entry -> event stream -> persisted transcript
- `GroupConversation -> TeamConversation + routing decision`
  - static mapping: group owns member list and route policy
  - dynamic usage: message -> route decision -> single-agent run OR orchestration task graph
- `OrchestrationPlan / TeamTask -> RuntimeTask`
  - static mapping: assignee employee -> assignee profile, parent steps -> runtime dependencies
  - dynamic usage: create task(s) -> dispatcher/worker -> result backflow
- `LoopMission -> RuntimeCronJob`
  - static mapping: mission record stores runtime job id
  - dynamic usage: schedule tick -> profile-scoped run -> output/status backflow
- `SkillGrant / ConnectorGrant / KnowledgeBinding / MemoryPolicy`
  - static mapping: business grant/policy -> runtime visibility/config/injection rule
  - dynamic usage: resolved during prompt assembly, tool loading, or execution bootstrap

## Reuse classification rule

Every mapping should be marked as one of:

- **Direct reuse** — already exists and can be called as-is
- **Extension reuse** — base exists, but product-layer adaptation is required
- **Net-new** — product must build this layer itself

This prevents a vague "we reuse X" statement from hiding real implementation cost.

## Source-verification rule

Do not stop at README or architecture prose. For each critical mapping, verify at least one real source-level anchor:

- function/method entry point
- route/controller
- persisted file/DB/table
- event type / stream callback
- worker spawn / scheduler path

## Worked example pattern

A common pattern is:

- mature **web UI** already has:
  - single-session workbench
  - SSE token/tool stream
  - settings/profile/workspace panels
- mature **runtime** already has:
  - single-agent entry
  - session persistence
  - task dispatcher / worker
  - cron scheduler
  - memory / skills / connectors
- product still must add:
  - business objects
  - business routing rules
  - team/group semantics
  - billing/audit/governance objects
  - northbound APIs and view models

That means the detailed design must not say only "build on top of the open-source project". It must say exactly:
- which existing surfaces are reused directly
- which existing surfaces are reused with extension
- which surfaces are entirely new in the product layer

## Common failure modes

- treating `profile` as if it were already a complete business employee object
- treating `session` as if it were already a complete business conversation object
- assuming group chat has a separate runtime just because the product has a separate page
- skipping the runtime handle field (`session_id`, `task_id`, `job_id`, etc.) in business records, which makes reconciliation impossible
- reporting a finished design while the formal artifact still lives in a temp path rather than the project's canonical design directory


# Runtime-backed derivative project calibration

Use this reference when a product is implemented as a downstream project built from an upstream runtime console or web interface.

## Canonical distinction

- **Upstream source base**: the codebase whose host shell, event-streaming engine, route organization, or UI framework is reused.
- **Downstream implementation carrier**: the actual project where the new product is developed and delivered.

Do not blur the two in detailed design.

## Example pattern

A product may define:

- upstream source base: `runtime-console`
- downstream implementation carrier: `product-service`
- internal module layers inside the downstream project:
  - `control-panel/`
  - `runtime-adapter/`
  - inherited host modules for routing, streaming, configuration, and static assets

## Wording rules for design documents

Prefer wording that states:

- the real implementation target
- which upstream base it reuses
- whether modules share one process or deploy independently
- which host capabilities are extended rather than rewritten

Avoid saying only that the team will "build on the upstream runtime." That wording hides the actual delivery repository and makes internal modules look like separate services when they may share one process.

## Reuse-anchor checklist

Identify which inherited capabilities remain the substrate, such as:

- server entrypoint
- route registration
- event streaming
- configuration loading
- model and profile handling
- upload and workspace handling
- static UI assets

List new business modules separately.

## Data-truth calibration

When the inherited host stores sessions in files but the product needs a control-plane database:

- state that host session storage is not the control-plane source of truth
- define the dual-track model explicitly:
  - control-plane truth in a transactional database
  - host or session mirror retained only for runtime compatibility

## Review prompts

1. Is the real target repository or project named?
2. Could a reader confuse internal modules with deployed services?
3. Are the inherited capabilities that remain the substrate explicit?
4. Is control-plane truth separated from host or session storage?
5. Is route growth additive, or does the design require a rewrite?

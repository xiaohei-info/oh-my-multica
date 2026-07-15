# Business control plane vs runtime gateway boundary

Use this reference when a product is built on an existing agent runtime and the design must decide whether the frontend should call the runtime gateway directly or go through a product-facing business layer.

## Evidence to inspect

### Runtime gateway responsibilities

Verify whether the gateway owns runtime concerns such as:

- model and capability discovery
- conversation and run creation
- event streaming
- approval and stop controls
- server-side tool execution
- runtime configuration and toolset selection

### Product business responsibilities

Check whether the gateway natively owns product concepts such as:

- users, teams, or employee definitions
- templates and solution bindings
- tenant or enterprise spaces
- permissions and governance
- business task semantics
- product-facing audit and cost controls

### Integration shape

Inspect whether the existing UI calls the runtime through HTTP, imports runtime modules in process, or uses another adapter. An existing UI integration proves only that one integration shape works; it does not prove that the same boundary fits a product with additional business semantics.

## Default architecture judgment

When the runtime gateway exposes execution primitives but does not own product business objects, use this boundary:

- **Business control plane**: owns product objects, policy, governance, and translation into runtime calls.
- **Runtime gateway**: exposes runtime access and control APIs.
- **Agent runtime**: executes tools and holds runtime truth.

The default request path is:

```text
Frontend -> Business control plane -> Runtime gateway -> Agent runtime
```

The business control plane and runtime gateway may still be separate peer subsystems in deployment and ownership terms. The ordering above describes the business request path.

## When direct frontend-to-gateway access is acceptable

Direct access can be reasonable when all of the following are true:

- the frontend is a runtime console rather than a business product
- runtime objects are the product objects
- no additional tenant, permission, governance, or business translation layer is required
- the gateway API is stable and appropriate for direct client exposure

## Review questions

1. Which layer owns product business objects?
2. Which layer owns runtime execution state?
3. Does the frontend need product semantics that the gateway does not expose?
4. Is authentication and authorization enforced at the correct boundary?
5. Would direct gateway access leak runtime concerns into the product UI?

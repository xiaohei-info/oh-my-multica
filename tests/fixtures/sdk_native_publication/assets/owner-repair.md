# Harness SDK production-build repair

## Repair and exact HEAD

The existing PR #90 remains on main at source revision and final submitted HEAD 1ebf12d36d29c2a3726257bd03685139f5bc568a; no PR update was made after these measurements. Its SDK diff is one line in sdk/harness/conformance.go:1402:

    - manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: operationPointer(), ExternalState: ExternalStateUnknown}
    + manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: &extension.ExternalOperation{SystemType: "fixture", ExternalID: "external-1"}, ExternalState: ExternalStateUnknown}

The replacement is the same ordinary ExternalOperation value previously provided by the test helper. The caller-constructed Unknown observation and following ErrRetryNotPermitted assertion remain unchanged.

## Before and after builds

Pre-repair source revision e3e6221ba743dcb1e851c535ef41d9da68aae89e was built with the same native Linux ARM64 Go 1.26.0 environment. The command go build ./sdk/harness/... exited 1 with:

    sdk/harness/conformance.go:1402:77: undefined: operationPointer

All 23 inherited verification commands then ran unchanged, in original order, with HEAD and source_revision equal to 1ebf12d36d29c2a3726257bd03685139f5bc568a. Every command exited 0, including both production builds and go test -count=1 ./.... Exact command strings, outputs, and exits are in verification-results.json and the OMAC verification.

## Coverage and conformance

The fresh unfiltered SDK-only profile is 1,933/2,262 statements (85.4553%) against the unchanged 85% gate. The fixed SDK gate records 50 required tests, 13 measured semantic rows, seven current-amendment rows, 15 service-mode cases, and four receipt-tree regressions.

SDK service and Gate rows remain negative-only for Host authority: registered_gate_positive_path_count=0 and authoritative_gate_positive_claim_count=0. Host-positive authority, Whole-SDK90, and Host90 remain mandatory downstream gates and are not claimed.

## Environment

- uname -sm: Linux aarch64.
- Go: go1.26.0 linux/arm64; go.mod pins go 1.26.0.
- go env: GOOS=linux, GOARCH=arm64, GOVERSION=go1.26.0, CGO_ENABLED=0, empty GOFLAGS, GOTOOLCHAIN=auto.
- Compiler: GCC 13.3.0; /proc/self/fd is available.
- The race-detector command ran with CGO_ENABLED=1; the default full suite ran with CGO_ENABLED=0.

The uncached full repository suite passed on native Linux ARM64. The filesystem package result establishes supported descriptor identity inspection and the existing typed mutation denial only; Supported/Supports remain false. No identity-atomic mutation capability or Workspace live conformance is claimed.

## Ownership, compatibility, and publication

Fresh component packaging remains harness-sdk / public-extension-sdk with source selection sdk/harness. This report, the source bundle, semantic evidence, source snapshot, and command results all identify 1ebf12d36d29c2a3726257bd03685139f5bc568a, which is also the submitted PR #90 HEAD. The immutable evidence publication is a separate commit and is identified by its exact commit in the OMAC verification; it does not move PR #90. The prior PR/main commits remain intact as historical evidence.

Compatibility review: no exported symbol, public API, wire field, JSON tag, canonical digest, Host ingress, seal, trust, replay rule, or permission behavior changes. No test authority is imported or moved, and no public helper is added. The shared measurement/seal algorithm itself is unchanged.

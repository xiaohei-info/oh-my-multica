# Harness SDK production-build repair

## Repair and exact HEAD

The existing PR #90 remains open on main at source revision and final submitted HEAD 1ebf12d36d29c2a3726257bd03685139f5bc568a. Its SDK diff is one line in sdk/harness/conformance.go:1402:

    - manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: operationPointer(), ExternalState: ExternalStateUnknown}
    + manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: &extension.ExternalOperation{SystemType: "fixture", ExternalID: "external-1"}, ExternalState: ExternalStateUnknown}

The replacement is the same ordinary ExternalOperation value previously provided by the test helper. The caller-constructed Unknown observation and following ErrRetryNotPermitted assertion remain unchanged.

## Before and after builds

Pre-repair source revision e3e6221ba743dcb1e851c535ef41d9da68aae89e was built with the same native Linux ARM64 Go 1.26.0 environment. The command go build ./sdk/harness/... exited 1 with:

    sdk/harness/conformance.go:1402:77: undefined: operationPointer

All 23 inherited verification commands ran unchanged, in original order, with HEAD and source_revision equal to 1ebf12d36d29c2a3726257bd03685139f5bc568a. Every command exited 0, including both production builds and go test -count=1 ./.... Exact command strings, outputs, per-command durations, hashes and exits are in verification-results.json.

The measured semantic evidence was copied immediately after command 18 and before command 23. The actual checkpoint SHA-256 is 5e709ad69d68c6971a655a70f75a3cb754930b9f0f7c864a6f8d29304ee27670 (169561 bytes) and it contains required_harness_conformance_test_count=50. Command 23 subsequently rewrote the test worktree default file to SHA-256 2dcaeae5cd056b2e0c06152fed7b155c73fd4e36a12b609fee14fa04ab90e307, which omits that fixed-selector metric. The immutable repair publication uses the validated command-18 checkpoint, not the post-suite output.

## Coverage and conformance

The fresh unfiltered SDK-only profile is 1,933/2,262 statements (85.4553%) against the unchanged 85% gate. The fixed SDK gate records 50 required tests, 13 measured semantic rows, seven current-amendment rows, 15 service-mode cases, and four receipt-tree regressions.

SDK service and Gate rows remain negative-only for Host authority: registered_gate_positive_path_count=0 and authoritative_gate_positive_claim_count=0. Host-positive authority, Whole-SDK90, and Host90 remain mandatory downstream gates and are not claimed.

## Environment

- uname -sm: Linux aarch64.
- Go: go1.26.0 linux/arm64; go.mod pins go 1.26.0.
- go env: GOOS=linux, GOARCH=arm64, GOVERSION=go1.26.0, CGO_ENABLED=0, empty GOFLAGS, GOTOOLCHAIN=auto.
- Compiler: GCC 13.3.0; /proc/self/fd is available.
- The exact race-detector command ran with CGO_ENABLED=1; the default full suite ran with CGO_ENABLED=0.

The uncached full repository suite passed on native Linux ARM64. The filesystem result establishes descriptor identity inspection and typed mutation denial only; Supported/Supports remain false. No identity-atomic mutation capability or Workspace live conformance is claimed.

## Ownership, compatibility, and publication

Fresh component packaging remains harness-sdk / public-extension-sdk with source selection sdk/harness. The component manifest, source bundle, semantic checkpoint, source snapshot and command results identify the same submitted PR HEAD above.

Compatibility and security review: no exported symbol, public API, wire field, JSON tag, canonical digest, Host ingress, seal, trust, replay rule, or permission behavior changes. No test authority is imported or moved, and no public helper is added. The shared recursive measurement/seal algorithm is unchanged.

Prior harness-sdk artifacts and the previous evidence publication remain historical. This new source-bound set preserves the measured command-18 semantic checkpoint without changing PR #90. Host-positive conformance and downstream coverage gates are not claimed.

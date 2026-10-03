# Harness SDK repair source snapshot

- Repair source commit and final submitted PR #90 HEAD: 1ebf12d36d29c2a3726257bd03685139f5bc568a.
- Rebased base, including merged Preview prerequisite: e3e6221ba743dcb1e851c535ef41d9da68aae89e.
- Existing PR: https://github.com/xiaohei-info/open-agent-cluster/pull/90.
- Source selection and component owner: sdk/harness/**; harness-sdk.
- Selected Go source files: 31.
- Sorted path and file-digest tree SHA-256: 78520de92125c85c6d5ddb39283172036548dd42c796d6c0d86bbb9215e925f0.
- SDK source bundle SHA-256: 51fe5c84fbaf2f962ced040c99940521c994614721955231afad23ce911f5887.
- Repair patch: 943 bytes, SHA-256 007550f79b42bb2ead97945d8dfe6c126ace20406544f6b32121d3778e30edbe.
- Deterministic gzip patch SHA-256: dececf1032705da4fcf2a4913ce9a289730ae562938cf88ff9e82d4a01788fba.

Only sdk/harness/conformance.go changes between the rebased base and PR HEAD. Its before blob is 00eee4a57636d2602b2049708910c75afc30733b; its after blob is f2ce8ed3e8e5c0112fcaa4ef82f97974d0f8cab5. The shared execution measurement tree is unchanged at blob 69112788a8da2b1427aa6e68153dded5ad3884d0.

The exact one-line source change is:

    - manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: operationPointer(), ExternalState: ExternalStateUnknown}
    + manualUnknown := ObserveResponse{Status: StatusUnknown, ExternalOperation: &extension.ExternalOperation{SystemType: "fixture", ExternalID: "external-1"}, ExternalState: ExternalStateUnknown}

The caller-constructed Unknown observation and following ErrRetryNotPermitted assertion remain. source-snapshot.json lists all selected files, byte counts, and SHA-256 values; source.patch.gz preserves the exact diff.

# Harness SDK repair source snapshot

- Repair source commit and submitted PR #90 HEAD: 1ebf12d36d29c2a3726257bd03685139f5bc568a.
- PR base commit: e3e6221ba743dcb1e851c535ef41d9da68aae89e.
- Existing PR: https://github.com/xiaohei-info/open-agent-cluster/pull/90.
- Source selection and package owner: sdk/harness/**; harness-sdk.
- Selected Go source files: 31.
- Sorted path and file-digest tree SHA-256: 78520de92125c85c6d5ddb39283172036548dd42c796d6c0d86bbb9215e925f0.
- SDK source bundle SHA-256: 51fe5c84fbaf2f962ced040c99940521c994614721955231afad23ce911f5887.
- Repair patch: 943 bytes, SHA-256 007550f79b42bb2ead97945d8dfe6c126ace20406544f6b32121d3778e30edbe.
- Deterministic gzip patch SHA-256: dececf1032705da4fcf2a4913ce9a289730ae562938cf88ff9e82d4a01788fba.

Only sdk/harness/conformance.go changes between the base and repair source. The before/after blobs and all 31 selected source file digests are in source-snapshot.json.

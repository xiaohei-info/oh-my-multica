# Harness SDK build repair coverage audit

## Source and raw profile

- Repair source revision and submitted PR #90 HEAD: 1ebf12d36d29c2a3726257bd03685139f5bc568a.
- Rebased base with merged Preview prerequisite: e3e6221ba743dcb1e851c535ef41d9da68aae89e.
- Source selection: sdk/harness/**; component owner harness-sdk.
- Raw coverage profile SHA-256: d86aca5fc0b7c9caa9d5781dd79ccac9b9b7769578976d65c314c5789ffba93e.
- SDK source tree SHA-256: 78520de92125c85c6d5ddb39283172036548dd42c796d6c0d86bbb9215e925f0.

## Result

The unfiltered atomic SDK-only profile covers 1,933 of 2,262 statements (85.4553%). The unchanged gate is 85%, requiring 1,923 covered statements; this result is 10 statements above the minimum. No package filtering, profile filtering, or denominator padding was used.

| File | Covered | Total | Coverage |
| --- | ---: | ---: | ---: |
| sdk/harness/conformance.go | 738 | 793 | 93.0643% |
| sdk/harness/execution_measurement_tree.go | 80 | 80 | 100.0000% |
| sdk/harness/execution_receipt.go | 146 | 208 | 70.1923% |
| sdk/harness/extension_points.go | 178 | 259 | 68.7259% |
| sdk/harness/harness.go | 562 | 639 | 87.9499% |
| sdk/harness/schema.go | 96 | 97 | 98.9691% |
| sdk/harness/service.go | 38 | 73 | 52.0548% |
| sdk/harness/typed_execution_receipt.go | 95 | 113 | 84.0708% |
| Total | 1,933 | 2,262 | 85.4553% |

The fixed suite records all 50 required test identifiers, 13 measured semantic rows, seven current-amendment rows, 15 service-mode cases, and four receipt-tree regressions. Their current source-bound transcripts are in this evidence set.

This is the SDK85 gate only. Whole-SDK90 and Host90 remain required in downstream Host gates. Host-positive behavior is not claimed by SDK evidence.

# Receipt-tree design evidence for the Harness SDK repair

## Exact source and PR binding

- Repair source revision and final submitted PR #90 HEAD: 1ebf12d36d29c2a3726257bd03685139f5bc568a.
- Rebased base: e3e6221ba743dcb1e851c535ef41d9da68aae89e.
- Only sdk/harness/conformance.go changes in the SDK source diff.
- execution_measurement_tree.go is unchanged at Git blob 69112788a8da2b1427aa6e68153dded5ad3884d0.
- execution_receipt.go is unchanged at Git blob 12c162ca55963d5c07d685763882abe1a339dcc9.
- typed_execution_receipt.go is unchanged at Git blob 93b8009e1a2b94b4b77f33bf41fd89ce78f6e25c.

## Algorithm and boundary

The shared execution-measurement tree remains the same private data-only recursive representation. It validates each measurement leaf and parent link, preserves child order, computes the existing canonical digest, and compares complete projections. Receipt validation still requires the existing per-node SDK seal and Host proof; measured data alone cannot authorize a receipt. Gate authority, service handoffs, public receipt signatures, JSON tags, and canonical digest bytes are unchanged.

The repair changes only one test-helper call in measuredRecoveryMatrixForExecutor to the same ordinary ExternalOperation value: SystemType fixture and ExternalID external-1. It preserves the caller-constructed Unknown observation and ErrRetryNotPermitted check. It adds no public helper and constructs no authority.

## Fresh regressions

The four receipt-tree regression tests passed at the exact submitted PR HEAD:

- TestHarnessSDKMeasuredTreeUsesProductionLineage
- TestHarnessSDKMeasuredTreeCopyAndParentBinding
- TestHarnessSDKMeasuredTreeCannotAuthorizeReceipt
- TestHarnessSDKReceiptProjectionCompatibility

The fresh JSON transcript is receipt-tree-tests.json. Host-positive and Host90 conformance remain downstream and are not claimed here.

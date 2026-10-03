# Harness SDK build repair coverage-block audit

## Source-bound result

- Repair source revision and submitted PR #90 HEAD: 1ebf12d36d29c2a3726257bd03685139f5bc568a.
- Raw profile SHA-256: d86aca5fc0b7c9caa9d5781dd79ccac9b9b7769578976d65c314c5789ffba93e.
- Uncovered profile blocks: 266.
- Uncovered statements: 329.
- Exact source spans and lexical caller matches: coverage-block-audit.json.

| Uncovered block classification | Statements | Blocks |
| --- | ---: | ---: |
| gate-proof | 90 | 70 |
| receipt-proof | 51 | 38 |
| scope-proof | 59 | 47 |
| service-proof | 43 | 32 |
| ordinary-conformance | 55 | 54 |
| ordinary-typed-data | 15 | 14 |
| mixed | 12 | 8 |
| unclassified | 4 | 3 |
| Total | 329 | 266 |

The conservative proof-only floor is 243 statements: 90 Gate, 51 receipt, 59 scope, and 43 service statements. These require existing Host proof, a registered Gate handoff, or an authenticated service handoff. The SDK runner cannot create that authority. This does not grant a Host-positive claim or relax downstream coverage gates.

The remaining 86 uncovered statements are outside that proof-only floor; the audit does not claim that each is reachable in ordinary SDK fixtures. JSON includes exact profile spans, source context, classification, and lexical call-site matches. Caller matching is lexical and does not resolve method receiver types.

No source code was moved or weakened to change the denominator. The unfiltered SDK-only 85% gate is unchanged; measured coverage is 1,933/2,262 (85.4553%).

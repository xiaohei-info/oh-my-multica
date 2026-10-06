# AITEAM-1055 malformed-marker fixture diagnosis

## Delivery identity

- PR: https://github.com/xiaohei-info/open-agent-cluster/pull/96 (open, ready for review, base `main`).
- Final tested source and delivery HEAD: `e9b4440f9c119275ccbacf989087b8b067385ca7`; tree `5e1ed8236e1e4f22058016ebc533e76be019b0c0`.
- Main base: `89f1854a46c4cfb59bc1a344d42d6513ca9d704c`; base tree `8a92e551bc827c8a6bfece4f4c4e2c1dfac74aaf`.
- Immutable evidence publication is separate from this source commit.

| Source path | Bytes | SHA256 |
|---|---:|---|
| `cmd/agentrun-job/adapters_test.go` | 69326 | `10c63ae380db347eb229c85af5e10ed81c1753b7e9eac8081c67b3a64a36976d` |
| `cmd/agentrun-job/main_test.go` | 79932 | `6eba639b39a97cc6a755b07bae265e6cc5559e6ff5653841861618c7a8014292` |
| `cmd/agentrun-job/testdata/malformed-marker/test_verify_evidence.py` | 10975 | `5773f34ef3a1344f007d2e27008f1bc199750b006072810a7522da695c0e6e4b` |
| `cmd/agentrun-job/testdata/malformed-marker/verify_evidence.py` | 58738 | `8162167b060a97df58fd4783a5c4c9c1a751bfbcfded2f338bcda3ff11e13aa9` |
| `cmd/agentrun-job/testdata/vendor/main.go` | 20139 | `fc7e1a9bb98520fba58c1ebc1a96869771653e3234f9dd9657984eabea1e603b` |

## Review blocker closures

The previous review identified three blockers. They are closed as follows:

1. **Held-Wait overwrite:** parent and nested invocations now use independent evidence paths; the nested legacy control receives `AGENTRUN_TEST_HELD_WAIT_EVIDENCE_PATH`. The final focused, scope, full-suite, and coverage snapshots each contain seven unique scenarios: startup without acceptance, malformed stream, legacy publication window, unwritten malformed bytes, failed trigger-marker publication, persistent append failure, and Runtime timeout. The verifier rejects missing or duplicate scenarios and checks the separate H prospective process-chain artifact.
2. **Built-chain negative controls:** the real built Job -> linked adapter -> configured Runtime path now has `malformed bytes remain unwritten` and `malformed trigger marker publication failure` schedules. They prove zero malformed bytes, failed marker publication, pending Wait, no false trigger/completion, process reaping, and post-release outcomes. Marker publication errors are surfaced by the testdata fixture.
3. **Trigger identity binding:** marker records use exact schemas and are atomically published. The verifier requires the expected per-scenario trigger ID and exact equality across prepared, write, cancellation, positive-trigger, and publication-failure records. Eight permanent Python negative controls cover foreign/mismatched triggers plus missing/drifted bytes, causal mappings, and immutable retrieval.

## Causal diagnosis

The reviewed PR90 source is `1ebf12d36d29c2a3726257bd03685139f5bc568a`, tree `8c1c9a6d8f392b3f23c73cd64ca104fbdbf3b80e`; all 88 input packet files and 28 PR90 source-index entries were previously hash/size verified. The bounded pre-fix overlay retained under `before-control/` reproduces the original missing-marker assertion at the eight-second observation after a successful malformed write and cancellation. This is a prospective fixture mechanism; the historical AITEAM-820 cancellation cause remains **unknown**, and marker ordering remains a hypothesis.

The repaired fixture distinguishes prepared input, successful malformed write, cancellation observation, positive rejection trigger, failed trigger publication, Runtime Wait, process reaping, Job return, terminal state, and completion. It uses same-directory temporary-file rename for evidence markers, so readers cannot observe partial JSON. Production AgentRun, Runtime SDK, release identity, and published adapter sources remain unchanged.

## Preserved attempts

- `0bbeed0b8a3f208638e036897e033a5c374cf9ab`: exit 1, verification-input contamination from `.go` overlay evidence files; preserved with log and metadata.
- `c106fbf6384b4d8e7c12c6af2c4823d6aefe5070`: exit 0, superseded successful validation; preserved.
- `4ec04ccdfe7d612837d04116277a100883409d8a`: exit 1, focused unwritten-input run caught a partial marker JSON read; retained with incomplete sibling snapshot and diagnosis.
- `f0263abee210d80e9854addd6d3bcafa2d731225`: targeted unwritten-input retest exit 0 after atomic marker publication; the concurrent hardening assertion ordering error is retained separately with its producer exit 0.
- Final source `985f5434766a58e45b0a2fa3768d5ee2a35cdf0c`: focused, scope, full-suite, coverage, hardening, release, package, and verifier gates pass.

## Environment and release

Native Linux/aarch64 (`linux/arm64`), Go 1.26.0, readable `/proc`, Docker 29.2.1 linux/arm64. The pinned release image is `golang@sha256:1ee99c345f8179002a0de40728e0eaa8865b81257b3924ef150ee65b3f1d3fc7`. The release manifest binds `e9b4440f9c119275ccbacf989087b8b067385ca7`, exactly `agentrun-job` and `agentrun-vendor`, zero bundled third-party Runtime, and the package verifier executed the shipped pair once without build or fixture staging. Combined statement coverage is **90.2%**.

## Exact contract command inventory

1. `GOTOOLCHAIN=go1.26.0 go version` — exit 0.
2. `mkdir -p artifacts/agentrun-malformed-marker-fixture && GOTOOLCHAIN=go1.26.0 go test -count=1 -json ./cmd/agentrun-job -run '^(TestMalformedStreamMarkerEvidenceControls|TestBuiltEntrypointHoldsOriginalWaitAcrossCleanupDeadlines|TestBuiltEntrypointCancelsVendorOnPersistentAppendFailure|TestBuiltEntrypointWaitsForRuntimeWaitOnPersistentAppendFailure)$' > artifacts/agentrun-malformed-marker-fixture/fixture-tests.json` — exit 0.
3. `python3 -c "import json; rows=[json.loads(x) for x in open('artifacts/agentrun-malformed-marker-fixture/fixture-tests.json') if x.strip()]; required={'TestMalformedStreamMarkerEvidenceControls','TestBuiltEntrypointHoldsOriginalWaitAcrossCleanupDeadlines','TestBuiltEntrypointCancelsVendorOnPersistentAppendFailure','TestBuiltEntrypointWaitsForRuntimeWaitOnPersistentAppendFailure'}; assert {r.get('Test') for r in rows if r.get('Action')=='pass' and r.get('Test') in required}==required; assert not any(r.get('Action') in ('fail','skip') for r in rows)"` — exit 0.
4. `cp artifacts/agentrun-cancellation-prerequisite/held-wait-process-evidence.jsonl artifacts/agentrun-malformed-marker-fixture/held-wait-focused.jsonl` — exit 0.
5. `GOTOOLCHAIN=go1.26.0 go test -count=1 -json ./runtime-sdk/... ./internal/agentrun/... ./cmd/agentrun-job/... > artifacts/agentrun-malformed-marker-fixture/uncached-scope-tests.json` — exit 0.
6. `cp artifacts/agentrun-cancellation-prerequisite/held-wait-process-evidence.jsonl artifacts/agentrun-malformed-marker-fixture/held-wait-scope.jsonl` — exit 0.
7. `GOTOOLCHAIN=go1.26.0 go vet ./runtime-sdk/... ./internal/agentrun/... ./cmd/agentrun-job/...` — exit 0.
8. `GOTOOLCHAIN=go1.26.0 go build ./cmd/agentrun-job/...` — exit 0.
9. `GOTOOLCHAIN=go1.26.0 go test -count=1 ./... > artifacts/agentrun-malformed-marker-fixture/first-full-suite.log 2>&1` — exit 0.
10. `cp artifacts/agentrun-cancellation-prerequisite/held-wait-process-evidence.jsonl artifacts/agentrun-malformed-marker-fixture/held-wait-full-suite.jsonl` — exit 0.
11. `mkdir -p artifacts/agentrun-malformed-marker-fixture/release && GOTOOLCHAIN=go1.26.0 go test -covermode=atomic -coverprofile=artifacts/agentrun-malformed-marker-fixture/release/coverage.out ./internal/agentrun/... ./cmd/agentrun-job/...` — exit 0.
12. `python3 -c "from pathlib import Path; import re,subprocess; p=Path('artifacts/agentrun-malformed-marker-fixture/release/coverage.out'); assert p.is_file() and p.stat().st_size>0; out=subprocess.check_output(['go','tool','cover','-func='+str(p)],text=True); m=re.search(r'^total:\s+\(statements\)\s+([0-9.]+)%$',out,re.M); assert m and float(m.group(1)) >= 90"` — exit 0.
13. `mkdir -p artifacts/agentrun-malformed-marker-fixture/release && GOTOOLCHAIN=go1.26.0 go test -json ./cmd/agentrun-job/... -run '^(TestProductionStreamJSONAdapterLaunchesConfiguredCommand|TestPublishedRuntimeAdapterContainsNoFixtureHooks|TestStreamJSONRejectsDuplicateAcceptanceAndPostTerminalData|TestStreamJSONCleansUpAcceptanceKindMismatch|TestStreamJSONRejectsForeignSessionEvents)$' -count=1 > artifacts/agentrun-malformed-marker-fixture/release/stream-json-hardening.json` — exit 0.
14. `python3 -c "import json; required={'TestStreamJSONRejectsDuplicateAcceptanceAndPostTerminalData', 'TestPublishedRuntimeAdapterContainsNoFixtureHooks', 'TestStreamJSONRejectsForeignSessionEvents', 'TestStreamJSONCleansUpAcceptanceKindMismatch', 'TestProductionStreamJSONAdapterLaunchesConfiguredCommand'}; rows=[json.loads(x) for x in open('artifacts/agentrun-malformed-marker-fixture/release/stream-json-hardening.json') if x.strip()]; passed={x.get('Test') for x in rows if x.get('Action')=='pass' and x.get('Test') in required}; assert passed==required, f'missing required passing stream-json tests: {sorted(required-passed)}'"` — exit 0.
15. `python3 -c "from pathlib import Path; roots=[Path('cmd/agentrun-job/vendor'),Path('cmd/agentrun-job/driver.go')]; files=[]; [files.extend(p for p in ([r] if r.is_file() else r.rglob('*')) if p.is_file() and 'testdata' not in p.parts and not p.name.endswith('_test.go')) for r in roots]; forbidden=('AGENTRUN_FIXTURE_MODE','AGENTRUN_FIXTURE_MARKER','AGENTRUN_FIXTURE_PID_FILE','blockForever'); hits=[f'{p}:{token}' for p in files for token in forbidden if token in p.read_text(errors='ignore')]; assert not hits, f'published fixture hooks: {hits}'"` — exit 0.
16. `test "$(docker run --rm --platform linux/arm64 golang@sha256:1ee99c345f8179002a0de40728e0eaa8865b81257b3924ef150ee65b3f1d3fc7 go version)" = "go version go1.26.0 linux/arm64"` — exit 0.
17. `python3 cmd/agentrun-job/build_release.py --source-revision HEAD --target linux/arm64 --build-image golang@sha256:1ee99c345f8179002a0de40728e0eaa8865b81257b3924ef150ee65b3f1d3fc7 --vendor-package ./cmd/agentrun-job/vendor --driver-identity cmd/agentrun-job/driver_identity.json --output artifacts/agentrun-malformed-marker-fixture/release --network-policy module-download-only` — exit 0.
18. `python3 -c "from pathlib import Path; import json; d=json.loads(Path('artifacts/agentrun-malformed-marker-fixture/release/runtime-driver-release-manifest.json').read_text()); assert d['build']=={'image':'golang@sha256:1ee99c345f8179002a0de40728e0eaa8865b81257b3924ef150ee65b3f1d3fc7','go_version':'1.26.0','goos':'linux','goarch':'arm64'}; assert set(d['binaries'])=={'agentrun-job','agentrun-vendor'}; assert d['vendor_source']=='cmd/agentrun-job/vendor' and d['vendor_role']=='stream-json-protocol-adapter' and d['bundled_third_party_runtime_count']==0"` — exit 0.
19. `python3 cmd/agentrun-job/verify_package.py --package-root artifacts/agentrun-malformed-marker-fixture/release --component-manifest artifacts/agentrun-malformed-marker-fixture/release/component-manifest.json --binaries-manifest artifacts/agentrun-malformed-marker-fixture/release/go-binaries-manifest.json --component-link artifacts/agentrun-malformed-marker-fixture/release/immutable-component-link.json --release-manifest artifacts/agentrun-malformed-marker-fixture/release/runtime-driver-release-manifest.json --driver-identity cmd/agentrun-job/driver_identity.json --expected-target linux/arm64 --expected-build-image golang@sha256:1ee99c345f8179002a0de40728e0eaa8865b81257b3924ef150ee65b3f1d3fc7 --execute-shipped-pair --forbid-build --forbid-fixture-staging --report artifacts/agentrun-malformed-marker-fixture/release/package-verification.json` — exit 0.
20. `python3 -c "from pathlib import Path; import json; r=json.loads(Path('artifacts/agentrun-malformed-marker-fixture/release/package-verification.json').read_text()); assert r['schema']=='oac.agentrun-package-verification/v1' and r['build_attempted'] is False and r['fixture_staging_attempted'] is False and r['shipped_pair_executed'] is True and r['process_tests_passed'] is True and r['published_fixture_hook_count']==0 and r['external_stream_json_command_executed'] is True and r['native_event_provenance_verified'] is True"` — exit 0.
21. `python3 cmd/agentrun-job/testdata/malformed-marker/verify_evidence.py --index artifacts/agentrun-malformed-marker-fixture/evidence-index.json --expected-source HEAD --require-immutable-publication` — exit 0.

The final raw artifacts, source hashes, preserved failure/pass attempts, seven-scenario snapshots, causal controls, H process-chain evidence, and release package are indexed with byte size, SHA256, and immutable Git publication locations by `evidence-index.json`. The final verifier requires exact source/tree equality and checks every indexed byte.

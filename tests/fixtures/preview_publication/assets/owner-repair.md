# Preview owner repair evidence

- Issue: AITEAM-1042
- PR: https://github.com/xiaohei-info/open-agent-cluster/pull/91 (open, non-draft, base `main`)
- Component/package owner: `release-preview`; DAG key `release-preview-audit-vocabulary-repair` is not a packaging owner.
- Before source revision: `dcb7d3a46a20e8f6d9e880cfab19b1c678f5eb03`
- After source revision / PR HEAD: `1dd603b45fe0ef9ac4bbc2ac75b0d1e64f2ed277`
- PR HEAD, committed source bundle manifest, and verification target refer to the same source revision.

## Changed source blob SHAs

- `internal/softwaredelivery/preview/decision_writer.go`: f5f4a362b1c2b089c906aa0969f91dbe3a06bed1 → c3d3cb0d3b4f918eaf9b2e869da13ef68c0d943e
- `internal/softwaredelivery/preview/acceptance_test.go`: 3cbb416f51fb2401972a6aeb9ff7fcc3f2968d27 → af837b0167a65e939865eab24c13acb91435ae3e
- `internal/softwaredelivery/preview/decision_writer_test.go`: 06c9c90557f713235c6b814f0afbabcf2bf1cab0 → 36e86fa559049face3864a2ccd6fe8bd02bdf5d2

## Root cause and repair

Preview's production `validateAuthorizationAudit` required authorization outcome `pass`, but the merged canonical Audit contract only accepts `allowed` or `denied` for authorization and admission. The acceptance fixture also emitted the undeclared `preview-decision/pass` control. The repair requires canonical `authorization/allowed`, keeps the existing policy revision/digest checks, and represents the decision using the existing AuditEvent action, subject, and event outcome.

The positive regression submits both `approve` and `request_changes` through the durable Preview store, checks the Decision, CommandRecord, AuditEvent, Outbox, and revision commit together, then reopens the store and verifies the complete persisted unit. Negative regressions reject `pass`, unknown and denied authorization outcomes, undeclared control identities, duplicate/conflicting authorization evidence, and mismatched policy revision/digest before any governed write.

## Acceptance reference traceability

- `UJ-RELEASE-CANDIDATE-001`: `internal/softwaredelivery/release/readiness_test.go::TestFormReleaseCreatesImmutableRelease`.
- `UJ-PREVIEW-FINAL-ACCEPTANCE-001`: the `approve` subtest of `TestPreviewCanonicalAuthorizationAuditAllowsGovernedCommit`, plus the exact-input-digest and decision-idempotency recovery regressions.
- `UJ-PREVIEW-REQUEST-CHANGES-001`: the `request_changes` subtest of `TestPreviewCanonicalAuthorizationAuditAllowsGovernedCommit`, plus `TestPreviewChangedFactsReturnStaleSubjectForApproveAndRequestChanges`.

## TDD red result

Before the production validator change, the three named canonical-audit tests were run with the declared JSON test command and exited 1. The positive approve and request_changes cases failed with `authorization audit control must pass`; the provenance test could not construct its valid fixture for the same reason. After the validator change, the exact command exited 0.

## API, security, and contract impact

No shared Audit contract, wire vocabulary, API/security semantics, persistence format, authorization policy, decision identity, exact subject/digest binding, replay, CAS, uniqueness, or restart behavior changed. The Preview consumer now requires the canonical `allowed` authorization outcome and still binds policy revision and digest to the existing AuthorizationEvidence. The canonical Audit validator continues to reject invalid/undeclared controls and duplicates. Existing tampering and rollback assertions remain in place.

## Environment and declared verification

- Environment: Go 1.26.5 (`darwin/arm64`), Python 3.14.6; race suite used `CGO_ENABLED=1`.
- Commands ran from the repository root at PR HEAD shown above.
- Combined Release/Preview statement coverage: 90.6%.

1. Exit 0: Release 90.2%, Preview 90.8%; both suites pass.
   `mkdir -p artifacts/release-preview-audit-vocabulary-repair && go test -covermode=atomic -coverprofile=artifacts/release-preview-audit-vocabulary-repair/coverage.out ./internal/softwaredelivery/release/... ./internal/softwaredelivery/preview/...`

2. Exit 0: Combined statement coverage is 90.6%.
   `python3 -c "from pathlib import Path; import re,subprocess; p=Path('artifacts/release-preview-audit-vocabulary-repair/coverage.out'); assert p.is_file() and p.stat().st_size>0; out=subprocess.check_output(['go','tool','cover','-func='+str(p)],text=True); m=re.search(r'^total:\s+\(statements\)\s+([0-9.]+)%$',out,re.M); assert m and float(m.group(1)) >= 90"`

3. Exit 0: Five required Preview recovery regressions pass; JSON transcript saved.
   `mkdir -p artifacts/release-preview-audit-vocabulary-repair && go test -json ./internal/softwaredelivery/release/... ./internal/softwaredelivery/preview/... -run '^(TestPreviewDecisionIdempotencyUsesCanonicalInputDigestNotClock|TestPreviewEvidenceSubjectRequiresExactInputDigestEquality|TestPreviewGeneratorRecordsExpectedActualRegressionDifference|TestPreviewDecisionIdentityReuseCannotOverwriteGovernedRecords|TestPreviewChangedFactsReturnStaleSubjectForApproveAndRequestChanges)$' -count=1 > artifacts/release-preview-audit-vocabulary-repair/preview-recovery-tests.json`

4. Exit 0: All five required recovery-test events are present and passing.
   `python3 -c "import json; required={'TestPreviewDecisionIdempotencyUsesCanonicalInputDigestNotClock', 'TestPreviewChangedFactsReturnStaleSubjectForApproveAndRequestChanges', 'TestPreviewGeneratorRecordsExpectedActualRegressionDifference', 'TestPreviewDecisionIdentityReuseCannotOverwriteGovernedRecords', 'TestPreviewEvidenceSubjectRequiresExactInputDigestEquality'}; rows=[json.loads(x) for x in open('artifacts/release-preview-audit-vocabulary-repair/preview-recovery-tests.json') if x.strip()]; passed={x.get('Test') for x in rows if x.get('Action')=='pass' and x.get('Test') in required}; assert passed==required, f'missing required passing preview recovery tests: {sorted(required-passed)}'"`

5. Exit 0: release-preview-owned source bundle and manifest packaged with all owner sources accounted.
   `python3 scripts/release/package_component.py --component release-preview --kind platform-core-dependency-source --input internal/softwaredelivery/release --input internal/softwaredelivery/preview --bundle artifacts/release-preview-audit-vocabulary-repair/source-bundle.tar.zst --component-manifest artifacts/release-preview-audit-vocabulary-repair/component-manifest.json --digest-output artifacts/release-preview-audit-vocabulary-repair/bundle-digest.txt --ownership-baseline contracts/release/source-ownership-baseline.yaml --owner release-preview --require-all-owner-sources-accounted`

6. Exit 0: Uncached Release, Preview, canonical Audit, and credential suites pass.
   `go test -count=1 ./internal/softwaredelivery/release/... ./internal/softwaredelivery/preview/... ./contracts/governance/audit/... ./contracts/governance/credential/...`

7. Exit 0: Preview vet passes.
   `go vet ./internal/softwaredelivery/preview/...`

8. Exit 0: Uncached Preview race suite passes.
   `CGO_ENABLED=1 go test -race -count=1 ./internal/softwaredelivery/preview/...`

9. Exit 0: All three named canonical-audit regressions pass; JSON transcript saved.
   `mkdir -p artifacts/release-preview-audit-vocabulary-repair && go test -json -count=1 ./internal/softwaredelivery/preview/... -run '^(TestPreviewCanonicalAuthorizationAuditAllowsGovernedCommit|TestPreviewCanonicalAuthorizationAuditRejectsNonCanonicalControls|TestPreviewCanonicalAuthorizationAuditRejectsConflictingProvenance)$' > artifacts/release-preview-audit-vocabulary-repair/canonical-audit-tests.json`

10. Exit 0: All three required test events are present; no fail or skip events.
   `python3 -c 'import json; from pathlib import Path; required={'"'"'TestPreviewCanonicalAuthorizationAuditRejectsConflictingProvenance'"'"', '"'"'TestPreviewCanonicalAuthorizationAuditRejectsNonCanonicalControls'"'"', '"'"'TestPreviewCanonicalAuthorizationAuditAllowsGovernedCommit'"'"'}; rows=[json.loads(x) for x in Path('"'"'artifacts/release-preview-audit-vocabulary-repair/canonical-audit-tests.json'"'"').read_text().splitlines() if x.strip()]; assert {r.get('"'"'Test'"'"') for r in rows if r.get('"'"'Action'"'"')=='"'"'pass'"'"' and r.get('"'"'Test'"'"') in required}==required; assert not any(r.get('"'"'Action'"'"') in {'"'"'fail'"'"','"'"'skip'"'"'} for r in rows)'`

## Published evidence

- GitHub Release: https://github.com/xiaohei-info/open-agent-cluster/releases/tag/aiteam-1042-repair-evidence-1dd603b45-r3
- Source revision: `1dd603b45fe0ef9ac4bbc2ac75b0d1e64f2ed277`
- Evidence index with per-file SHA-256, byte size, and direct URL: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/evidence-index.json
- Complete archive: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/release-preview-audit-vocabulary-repair-evidence.tar.gz
- Verification integration-gate artifact lists retain the original local names and include these corresponding public URLs:
- `coverage.out`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/coverage.out
- `preview-recovery-tests.json`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/preview-recovery-tests.json
- `canonical-audit-tests.json`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/canonical-audit-tests.json
- `source-bundle.tar.zst`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/source-bundle.tar.zst
- `component-manifest.json`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/component-manifest.json
- `bundle-digest.txt`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/bundle-digest.txt
- `owner-repair.md`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/owner-repair.md
- `verification.yaml`: https://github.com/xiaohei-info/open-agent-cluster/releases/download/aiteam-1042-repair-evidence-1dd603b45-r3/verification.yaml
## Immutable artifact digests

- Source bundle manifest revision: `1dd603b45fe0ef9ac4bbc2ac75b0d1e64f2ed277`
- Source bundle digest: `efc76f0786f0d0adf20a5c35c0d74da2a74fd9e31e4cd48f53681a0b097ec790`
- `coverage.out`: sha256 `0dbea4c91cbd479c80f8342c1a106c91a3287992c3fa7a93f728e1f298196ead`, 127303 bytes
- `preview-recovery-tests.json`: sha256 `eb3a2c602f02994bb54c914942cd66bd542b8c9847d0c272021a4b84cb94428b`, 9466 bytes
- `source-bundle.tar.zst`: sha256 `efc76f0786f0d0adf20a5c35c0d74da2a74fd9e31e4cd48f53681a0b097ec790`, 38195 bytes
- `bundle-digest.txt`: sha256 `95a7cdd1e83fbe0a8afe5cff82aab5a566a84dd7090e035b3293ed4082b9a847`, 65 bytes
- `component-manifest.json`: sha256 `72404e9b0b46fec85196ba86c5b72fbf9381dc878bf1dd0d155690b247e342af`, 2034 bytes
- `canonical-audit-tests.json`: sha256 `68e0ae751d444dd194091a9b38597ce281574ede72be7e89274b723ae4932091`, 16265 bytes

- `verification.yaml`: sha256 `b044e29bcfabd8da6715a179f10e0e64993a44bee7279eee4176268ab7467937`, 14758 bytes

## Source patch

```diff
diff --git a/internal/softwaredelivery/preview/acceptance_test.go b/internal/softwaredelivery/preview/acceptance_test.go
index 3cbb416f5..af837b016 100644
--- a/internal/softwaredelivery/preview/acceptance_test.go
+++ b/internal/softwaredelivery/preview/acceptance_test.go
@@ -481,13 +481,27 @@ func (writer *previewWriter) SubmitPreviewAcceptanceDecision(_ context.Context,
 }
 
 type previewAuditFactory struct {
-	err error
+	err                  error
+	authorizationOutcome string
+	additionalControls   []governance.EvaluatedControl
 }
 
 func (factory previewAuditFactory) PreparePreviewAcceptanceDecision(_ context.Context, authorization DecisionAuthorization) (governance.AuditEvent, error) {
 	if factory.err != nil {
 		return governance.AuditEvent{}, factory.err
 	}
+	outcome := factory.authorizationOutcome
+	if outcome == "" {
+		outcome = "allowed"
+	}
+	controls := append([]governance.EvaluatedControl{{
+		Control:        "authorization",
+		Outcome:        outcome,
+		PolicyID:       "preview-decision-authorization",
+		PolicyRevision: int64(authorization.Evidence.AuthorizationVersion),
+		PolicyDigest:   authorization.Evidence.AuthorizationDigest,
+		MatchedGrantID: authorization.Decision.Principal + "-preview-decision",
+	}}, factory.additionalControls...)
 	return governance.AuditEvent{
 		AuditEventID:     "audit-" + authorization.Decision.ID,
 		RequestContextID: "request-context-1",
@@ -498,23 +512,10 @@ func (factory previewAuditFactory) PreparePreviewAcceptanceDecision(_ context.Co
 			ID:     authorization.Decision.ID,
 			Digest: authorization.Decision.DecisionDigest,
 		},
-		Outcome: "succeeded",
-		EvaluatedControls: []governance.EvaluatedControl{
-			{
-				Control:        "authorization",
-				Outcome:        "pass",
-				PolicyID:       "preview-decision-authorization",
-				PolicyRevision: int64(authorization.Evidence.AuthorizationVersion),
-				PolicyDigest:   authorization.Evidence.AuthorizationDigest,
-				MatchedGrantID: authorization.Decision.Principal + "-preview-decision",
-			},
-			{
-				Control: "preview-decision",
-				Outcome: "pass",
-			},
-		},
-		ChangeSummary: "submitted Preview acceptance decision",
-		OccurredAt:    authorization.Decision.CreatedAt,
+		Outcome:           "succeeded",
+		EvaluatedControls: controls,
+		ChangeSummary:     "submitted Preview acceptance decision",
+		OccurredAt:        authorization.Decision.CreatedAt,
 	}, nil
 }
 
@@ -576,6 +577,76 @@ func TestPreviewDecisionIdempotencyUsesCanonicalInputDigestNotClock(t *testing.T
 	}
 }
 
+func TestPreviewCanonicalAuthorizationAuditAllowsGovernedCommit(t *testing.T) {
+	for _, decisionType := range []resource.DecisionType{resource.DecisionTypeApprove, resource.DecisionTypeRequestChanges} {
+		t.Run(string(decisionType), func(t *testing.T) {
+			facts := validPreviewFacts(t)
+			service, store := durableDecisionServiceWithAudit(t, facts, previewAuditFactory{})
+			view, err := BuildPreviewAcceptance(facts)
+			if err != nil {
+				t.Fatal(err)
+			}
+			request := validDecisionRequest(view, decisionType)
+			if decisionType == resource.DecisionTypeRequestChanges {
+				request.Reason = "update the technical design for the current Preview findings"
+				request.RequestChangesRoute = validRoute()
+			}
+
+			decision, err := service.SubmitDecision(context.Background(), request)
+			if err != nil {
+				t.Fatalf("submit %s with canonical authorization: %v", decisionType, err)
+			}
+			if decision.Type != decisionType || len(store.commitLog) != 1 || len(store.decisions) != 1 || len(store.commandRecords) != 1 || len(store.auditEvents) != 1 || len(store.outbox) != 1 || store.revision != facts.WorkUnitRevision+1 {
+				t.Fatalf("governed %s commit incomplete: decision=%#v commits=%d decisions=%d commands=%d audits=%d outbox=%d revision=%d", decisionType, decision, len(store.commitLog), len(store.decisions), len(store.commandRecords), len(store.auditEvents), len(store.outbox), store.revision)
+			}
+			commit := store.commitLog[0]
+			if commit.Decision.ID != decision.ID || len(commit.AuditEvent.EvaluatedControls) != 1 || commit.AuditEvent.EvaluatedControls[0].Control != "authorization" || commit.AuditEvent.EvaluatedControls[0].Outcome != "allowed" {
+				t.Fatalf("committed audit does not use canonical authorization evidence: %#v", commit.AuditEvent)
+			}
+
+			restarted, err := NewDurablePlatformDecisionCommitStore(store.statePath, &previewFactsResolver{facts: facts}, facts.WorkUnitRevision)
+			if err != nil {
+				t.Fatalf("restart durable store: %v", err)
+			}
+			if len(restarted.commitLog) != 1 || len(restarted.decisions) != 1 || len(restarted.commandRecords) != 1 || len(restarted.auditEvents) != 1 || len(restarted.outbox) != 1 || restarted.revision != facts.WorkUnitRevision+1 {
+				t.Fatalf("restarted %s commit incomplete: commits=%d decisions=%d commands=%d audits=%d outbox=%d revision=%d", decisionType, len(restarted.commitLog), len(restarted.decisions), len(restarted.commandRecords), len(restarted.auditEvents), len(restarted.outbox), restarted.revision)
+			}
+		})
+	}
+}
+
+func TestPreviewCanonicalAuthorizationAuditRejectsNonCanonicalControls(t *testing.T) {
+	tests := []struct {
+		name                 string
+		authorizationOutcome string
+		additionalControls   []governance.EvaluatedControl
+	}{
+		{name: "pass outcome", authorizationOutcome: "pass"},
+		{name: "unknown outcome", authorizationOutcome: "unknown"},
+		{name: "denied authorization", authorizationOutcome: "denied"},
+		{name: "undeclared control identity", additionalControls: []governance.EvaluatedControl{{Control: "preview-decision", Outcome: "allowed"}}},
+	}
+	for _, test := range tests {
+		t.Run(test.name, func(t *testing.T) {
+			facts := validPreviewFacts(t)
+			service, store := durableDecisionServiceWithAudit(t, facts, previewAuditFactory{
+				authorizationOutcome: test.authorizationOutcome,
+				additionalControls:   test.additionalControls,
+			})
+			view, err := BuildPreviewAcceptance(facts)
+			if err != nil {
+				t.Fatal(err)
+			}
+			if _, err := service.SubmitDecision(context.Background(), validDecisionRequest(view, resource.DecisionTypeApprove)); err == nil {
+				t.Fatal("noncanonical authorization audit reached the governed write")
+			}
+			if len(store.commitLog) != 0 || len(store.decisions) != 0 || len(store.commandRecords) != 0 || len(store.auditEvents) != 0 || len(store.outbox) != 0 || store.revision != facts.WorkUnitRevision {
+				t.Fatalf("rejected audit left governed writes: commits=%d decisions=%d commands=%d audits=%d outbox=%d revision=%d", len(store.commitLog), len(store.decisions), len(store.commandRecords), len(store.auditEvents), len(store.outbox), store.revision)
+			}
+		})
+	}
+}
+
 func TestPreviewDecisionReplaySurvivesAdvancedRevisionForApproveAndRequestChanges(t *testing.T) {
 	for _, decisionType := range []resource.DecisionType{resource.DecisionTypeApprove, resource.DecisionTypeRequestChanges} {
 		t.Run(string(decisionType), func(t *testing.T) {
@@ -733,6 +804,30 @@ func platformDecisionService(t *testing.T, facts PreviewAcceptanceFacts) (*Decis
 	return service, store
 }
 
+func durableDecisionServiceWithAudit(t *testing.T, facts PreviewAcceptanceFacts, audit previewAuditFactory) (*DecisionService, *PlatformDecisionCommitStore) {
+	t.Helper()
+	resolver := &previewFactsResolver{facts: facts}
+	store, err := NewDurablePlatformDecisionCommitStore(t.TempDir()+"/preview-decisions.json", resolver, facts.WorkUnitRevision)
+	if err != nil {
+		t.Fatalf("NewDurablePlatformDecisionCommitStore: %v", err)
+	}
+	writer, err := NewPlatformDecisionWriter(store)
+	if err != nil {
+		t.Fatalf("NewPlatformDecisionWriter: %v", err)
+	}
+	service, err := NewDecisionService(DecisionDependencies{
+		FactsResolver: resolver,
+		Authorizer:    &previewAuthorizer{},
+		Audit:         audit,
+		Writer:        writer,
+		Now:           func() time.Time { return time.Date(2026, time.August, 17, 9, 0, 0, 0, time.UTC) },
+	})
+	if err != nil {
+		t.Fatalf("NewDecisionService with durable writer: %v", err)
+	}
+	return service, store
+}
+
 func TestDecisionServiceRechecksExactSubjectAndSupportsBothDecisions(t *testing.T) {
 	facts := validPreviewFacts(t)
 	view, err := BuildPreviewAcceptance(facts)
diff --git a/internal/softwaredelivery/preview/decision_writer.go b/internal/softwaredelivery/preview/decision_writer.go
index f5f4a362b..c3d3cb0d3 100644
--- a/internal/softwaredelivery/preview/decision_writer.go
+++ b/internal/softwaredelivery/preview/decision_writer.go
@@ -365,8 +365,8 @@ func validateAuthorizationAudit(event governance.AuditEvent, evidence Authorizat
 		if control.Control != "authorization" {
 			continue
 		}
-		if control.Outcome != "pass" {
-			return fmt.Errorf("authorization audit control must pass")
+		if control.Outcome != string(governance.ControlOutcomeAllowed) {
+			return fmt.Errorf("authorization audit control must be allowed")
 		}
 		if control.PolicyRevision < 0 || uint64(control.PolicyRevision) != evidence.AuthorizationVersion {
 			return fmt.Errorf("authorization audit control version does not match evidence")
diff --git a/internal/softwaredelivery/preview/decision_writer_test.go b/internal/softwaredelivery/preview/decision_writer_test.go
index 06c9c9055..36e86fa55 100644
--- a/internal/softwaredelivery/preview/decision_writer_test.go
+++ b/internal/softwaredelivery/preview/decision_writer_test.go
@@ -192,6 +192,74 @@ func TestAuthorizationEvidenceAndAuditBindingRejectTampering(t *testing.T) {
 	}
 }
 
+func TestPreviewCanonicalAuthorizationAuditRejectsConflictingProvenance(t *testing.T) {
+	commit, submission := platformCommitFixture(t)
+	tests := []struct {
+		name   string
+		mutate func(*governance.AuditEvent)
+	}{
+		{
+			name: "duplicate authorization evidence",
+			mutate: func(event *governance.AuditEvent) {
+				event.EvaluatedControls = append(event.EvaluatedControls, event.EvaluatedControls[0])
+			},
+		},
+		{
+			name: "conflicting duplicate provenance",
+			mutate: func(event *governance.AuditEvent) {
+				conflict := event.EvaluatedControls[0]
+				conflict.PolicyRevision++
+				event.EvaluatedControls = append(event.EvaluatedControls, conflict)
+			},
+		},
+		{
+			name: "authorization policy version",
+			mutate: func(event *governance.AuditEvent) {
+				event.EvaluatedControls[0].PolicyRevision++
+			},
+		},
+		{
+			name: "authorization policy digest",
+			mutate: func(event *governance.AuditEvent) {
+				event.EvaluatedControls[0].PolicyDigest = previewDigest("f")
+			},
+		},
+	}
+	for _, test := range tests {
+		t.Run(test.name, func(t *testing.T) {
+			candidateSubmission := submission
+			candidateSubmission.AuditEvent.EvaluatedControls = append([]governance.EvaluatedControl(nil), submission.AuditEvent.EvaluatedControls...)
+			test.mutate(&candidateSubmission.AuditEvent)
+			storeCalls := 0
+			writer, err := NewPlatformDecisionWriter(decisionStoreFunc(func(context.Context, DecisionCommit) (resource.Decision, error) {
+				storeCalls++
+				return commit.Decision, nil
+			}))
+			if err != nil {
+				t.Fatal(err)
+			}
+			if _, err := writer.SubmitPreviewAcceptanceDecision(context.Background(), candidateSubmission); err == nil || storeCalls != 0 {
+				t.Fatalf("conflicting submission reached store: err=%v store calls=%d", err, storeCalls)
+			}
+
+			candidateCommit := commit
+			candidateCommit.AuditEvent.EvaluatedControls = append([]governance.EvaluatedControl(nil), commit.AuditEvent.EvaluatedControls...)
+			test.mutate(&candidateCommit.AuditEvent)
+			facts := validPreviewFacts(t)
+			store, err := NewPlatformDecisionCommitStore(&previewFactsResolver{facts: facts}, facts.WorkUnitRevision)
+			if err != nil {
+				t.Fatal(err)
+			}
+			if _, err := store.CommitPreviewAcceptanceDecision(context.Background(), candidateCommit); err == nil {
+				t.Fatal("conflicting commit provenance was accepted")
+			}
+			if len(store.commitLog) != 0 || len(store.decisions) != 0 || len(store.commandRecords) != 0 || len(store.auditEvents) != 0 || len(store.outbox) != 0 || store.revision != facts.WorkUnitRevision {
+				t.Fatalf("rejected commit left state: commits=%d decisions=%d commands=%d audits=%d outbox=%d revision=%d", len(store.commitLog), len(store.decisions), len(store.commandRecords), len(store.auditEvents), len(store.outbox), store.revision)
+			}
+		})
+	}
+}
+
 func TestPlatformDecisionWriterRejectsStoreFailuresAndInvalidResults(t *testing.T) {
 	commit, submission := platformCommitFixture(t)
 	failingWriter, err := NewPlatformDecisionWriter(decisionStoreFunc(func(context.Context, DecisionCommit) (resource.Decision, error) {
```

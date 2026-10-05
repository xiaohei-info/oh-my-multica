package audit

import (
	"testing"
	"time"
)

// BLK-0957c2bad42f: per-export-type authentication, optional credentials,
// binding subject = target identity, digest covers CredentialBinding identity,
// save/update/replace CAS contracts, and filter-only export.

func TestAuditExportTypeAuthenticationMatrix(t *testing.T) {
	if !ExportTypeWebhook.RequiresAuthentication() {
		t.Fatal("webhook export type must declare authentication requirement")
	}
	if ExportTypeSyslog.RequiresAuthentication() {
		t.Fatal("syslog export type must not declare authentication requirement")
	}
	if ExportType("made-up").RequiresAuthentication() {
		t.Fatal("unknown export type cannot authenticate")
	}
}

func TestAuditExportCredentialsOptionalOnlyWherePermitted(t *testing.T) {
	webhookConfiguration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	syslogConfiguration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}

	webhookDigest, err := webhookConfiguration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	syslogDigest, err := syslogConfiguration.DigestFor(ExportTypeSyslog, "")
	if err != nil {
		t.Fatal(err)
	}

	// A webhook target without a credential binding is rejected.
	if err := (AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: webhookConfiguration, ConfigurationDigest: webhookDigest, Revision: "1"}).Validate(); err == nil {
		t.Fatal("webhook target without credential binding must be rejected")
	}
	// A syslog target without a credential binding is valid.
	if err := (AuditExportTarget{ID: "target-2", ScopeID: "scope-1", Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: syslogConfiguration, ConfigurationDigest: syslogDigest, Revision: "1"}).Validate(); err != nil {
		t.Fatalf("syslog target without credential binding must be valid: %v", err)
	}
	// A syslog target with a credential binding is rejected (not permitted).
	syslogWithBindingDigest, err := syslogConfiguration.DigestFor(ExportTypeSyslog, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	if err := (AuditExportTarget{ID: "target-3", ScopeID: "scope-1", Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: syslogConfiguration, ConfigurationDigest: syslogWithBindingDigest, CredentialBindingID: "binding-1", Revision: "1"}).Validate(); err == nil {
		t.Fatal("syslog target with credential binding must be rejected")
	}
}

func TestAuditExportDigestCoversCredentialBindingIdentity(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	withoutBinding, err := configuration.DigestFor(ExportTypeWebhook, "")
	if err != nil {
		t.Fatal(err)
	}
	withBinding, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	if withoutBinding == withBinding {
		t.Fatal("configuration digest must cover the CredentialBinding identity")
	}
	otherBinding, err := configuration.DigestFor(ExportTypeWebhook, "binding-2")
	if err != nil {
		t.Fatal(err)
	}
	if otherBinding == withBinding {
		t.Fatal("configuration digest must distinguish credential bindings")
	}
	// name / description / enabled must not change the digest.
	candidate := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	if err := candidate.Validate(); err != nil {
		t.Fatalf("candidate Validate() error = %v", err)
	}
	candidate.Name = "renamed"
	candidate.Description = "changed"
	if err := candidate.Validate(); err != nil {
		t.Fatalf("name/description must not change the digest: %v", err)
	}
}

func TestAuditExportBindingSubjectIsTargetIdentity(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	candidate := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	// The binding subject must be the export target, not a caller principal.
	callerPrincipalBinding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "principal-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	if err := candidate.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: callerPrincipalBinding}}, "target-1", "scope-1"); err == nil {
		t.Fatal("credential bound to a caller principal must not authorize the target")
	}
	targetBinding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "target-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	if err := candidate.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: targetBinding}}, "target-1", "scope-1"); err != nil {
		t.Fatalf("credential bound to the target must authorize the export: %v", err)
	}
	// The purpose must match the export target.
	wrongPurpose := targetBinding
	wrongPurpose.Usages = nil
	if err := candidate.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: wrongPurpose}}, "target-1", "scope-1"); err == nil {
		t.Fatal("binding without the audit-export purpose must not authorize the target")
	}
}

func TestAuditExportSaveUpdateEnableReplaceContracts(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	candidate := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	binding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "target-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	policy := ExportPolicy{MaxRows: 10, MaxBytes: 1024, CredentialBindings: auditBindingResolver{binding: binding}}
	currentTargetDigest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	currentTarget := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: currentTargetDigest, CredentialBindingID: "binding-1", Revision: "5"}

	// Create: expected revision empty, target defaults disabled.
	save := SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate}
	if err := save.ValidateWithPolicy(policy, "target-1"); err != nil {
		t.Fatalf("save create ValidateWithPolicy() error = %v", err)
	}
	// Update: CAS revision required.
	update := UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", ExpectedRevision: "4", Name: "renamed", Description: "changed"}
	if err := update.Validate(); err != nil {
		t.Fatalf("update Validate() error = %v", err)
	}
	if err := (UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", Name: "renamed", Description: "changed"}).Validate(); err == nil {
		t.Fatal("update without CAS revision must be rejected")
	}
	// Enable/disable: CAS revision required.
	if err := (EnableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", ExpectedRevision: "4", ConfigurationDigest: "sha256:config"}).Validate(); err != nil {
		t.Fatalf("enable Validate() error = %v", err)
	}
	if err := (DisableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", ExpectedRevision: "5", ConfigurationDigest: "sha256:config"}).Validate(); err != nil {
		t.Fatalf("disable Validate() error = %v", err)
	}
	if err := (EnableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", ConfigurationDigest: "sha256:config"}).Validate(); err == nil {
		t.Fatal("enable without CAS revision must be rejected")
	}
	// Replace: new target identity, old target CAS-bound and disabled.
	replacementConfiguration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	replacement := AuditExportTargetDraft{Name: "syslog", Description: "replacement", ExportType: ExportTypeSyslog, Configuration: replacementConfiguration}
	replace := ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), CurrentTargetID: "target-1", ExpectedRevision: "5", Replacement: replacement}
	if err := replace.Validate(); err != nil {
		t.Fatalf("replace Validate() error = %v", err)
	}
	if err := replace.ValidateWithPolicy(policy, "target-2", currentTarget); err != nil {
		t.Fatalf("replace ValidateWithPolicy() error = %v", err)
	}
	wrongCurrentTarget := currentTarget
	wrongCurrentTarget.ID = "target-other"
	if err := replace.ValidateWithPolicy(policy, "target-2", wrongCurrentTarget); err == nil {
		t.Fatal("replacement accepted a revision from a different current target")
	}
	if err := (ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), CurrentTargetID: "target-1", Replacement: replacement}).Validate(); err == nil {
		t.Fatal("replace without CAS revision must be rejected")
	}
}

func TestAuditExportRejectsPaginationSemantics(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	request := ExportAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, Format: ExportFormatJSONL}
	if err := request.ValidateWithPolicy(DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("ValidateWithPolicy() error = %v", err)
	}
	// The export contract must not carry cursor or page-size semantics.
	request.Filter.TimeRange.End = start.Add(DefaultQueryPolicy().MaxTimeRange + time.Nanosecond)
	if err := request.ValidateWithPolicy(DefaultQueryPolicy(), DefaultExportPolicy()); err == nil {
		t.Fatal("export must be bounded by the server time range policy")
	}
	if err := (ExportAuditEventsRequest{Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, Format: ExportFormatCSV}).Validate(); err == nil {
		t.Fatal("export without request context must be rejected")
	}
	if err := (ExportAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, Format: ExportFormat("xml")}).Validate(); err == nil {
		t.Fatal("export without a supported format must be rejected")
	}
}

// BLK-739524de8005: immutable validated query state and page validation with
// cursor linkage, filter membership, ordering, duplicates, and seek follow.

func TestValidatedAuditQueryIsImmutable(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, Cursor: "issued", PageSize: 25}
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: "issued", binding: auditCursorBinding(request), position: CursorPosition{OccurredAt: start, RecordID: "audit-1"}}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	// Mutating the returned request copy must not affect the validated query.
	copy := query.Request()
	copy.Filter.ProjectID = "project-2"
	copy.PageSize = 999
	if query.Request().Filter.ProjectID != "" || query.Request().PageSize != 25 {
		t.Fatal("validated query state must be immutable to consumers")
	}
	seek := query.Seek()
	if seek == nil || seek.OccurredAt != start.UTC() || seek.RecordID != "audit-1" {
		t.Fatalf("Seek() = %+v", seek)
	}
	seek.RecordID = "tampered"
	if query.Seek().RecordID != "audit-1" {
		t.Fatal("Seek() must return a defensive copy")
	}
	if query.ScopeID() != "scope-1" || query.PageSize() != 25 || query.Filter().ProjectID != "" {
		t.Fatal("validated query accessors must reflect the validated request")
	}
}

func TestAuditEventPageValidatesBoundFilterOrderingAndCursorLinkage(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(24 * time.Hour)}, ProjectID: "project-1"}, PageSize: 2}
	first := exportSummary(validAuditEvent("audit-2", start.Add(2*time.Hour)))
	second := exportSummary(validAuditEvent("audit-1", start.Add(1*time.Hour)))
	first.ProjectID, second.ProjectID = "project-1", "project-1"
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: "next", binding: auditCursorBinding(request), position: CursorPosition{OccurredAt: second.OccurredAt, RecordID: second.ID}}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	page := AuditEventPage{Items: []AuditEventSummary{first, second}, NextCursor: ""}
	if err := page.Validate(query, policy); err != nil {
		t.Fatalf("valid page Validate() error = %v", err)
	}
	// Out-of-order page.
	unordered := AuditEventPage{Items: []AuditEventSummary{second, first}}
	if err := unordered.Validate(query, policy); err == nil {
		t.Fatal("unordered page must be rejected")
	}
	// Duplicate page.
	duplicate := AuditEventPage{Items: []AuditEventSummary{first, first}}
	if err := duplicate.Validate(query, policy); err == nil {
		t.Fatal("duplicate page must be rejected")
	}
	// Item outside the bound filter.
	outside := first
	outside.ProjectID = "project-2"
	if err := (AuditEventPage{Items: []AuditEventSummary{outside, second}}).Validate(query, policy); err == nil {
		t.Fatal("item outside the authenticated filter must be rejected")
	}
	// Item outside the bound time range.
	tooOld := exportSummary(validAuditEvent("audit-0", start.Add(-time.Hour)))
	tooOld.ProjectID = "project-1"
	if err := (AuditEventPage{Items: []AuditEventSummary{first, tooOld}}).Validate(query, policy); err == nil {
		t.Fatal("item outside the bound time range must be rejected")
	}
	// Page size violation.
	if err := (AuditEventPage{Items: []AuditEventSummary{first, second, tooOld}}).Validate(query, policy); err == nil {
		t.Fatal("page exceeding page size must be rejected")
	}
	// Next cursor must be the cursor of the actual final item.
	page.NextCursor = "wrong"
	if err := page.Validate(query, policy); err == nil {
		t.Fatal("next cursor not issued from the actual final item must be rejected")
	}
	page.NextCursor = "next"
	if err := page.Validate(query, policy); err != nil {
		t.Fatalf("linked next cursor Validate() error = %v", err)
	}
}

func TestAuditEventPageFollowsAuthenticatedSeek(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(24 * time.Hour)}}, Cursor: "seek", PageSize: 2}
	policy := DefaultQueryPolicy()
	seekPosition := CursorPosition{OccurredAt: start.Add(2 * time.Hour), RecordID: "audit-2"}
	policy.CursorCodec = auditTestCursorCodec{token: "seek", binding: auditCursorBinding(request), position: seekPosition}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	newer := exportSummary(validAuditEvent("audit-3", start.Add(3*time.Hour)))
	if err := (AuditEventPage{Items: []AuditEventSummary{newer}}).Validate(query, policy); err == nil {
		t.Fatal("page with an item newer than the authenticated seek must be rejected")
	}
	older := exportSummary(validAuditEvent("audit-1", start.Add(1*time.Hour)))
	if err := (AuditEventPage{Items: []AuditEventSummary{older}}).Validate(query, policy); err != nil {
		t.Fatalf("page following the authenticated seek must be valid: %v", err)
	}
	// Same-instant tie-break: the next page uses the record id.
	sameInstant := exportSummary(validAuditEvent("audit-1", seekPosition.OccurredAt))
	if err := (AuditEventPage{Items: []AuditEventSummary{sameInstant}}).Validate(query, policy); err != nil {
		t.Fatalf("same-instant id-tie page Validate() error = %v", err)
	}
	// Newer concurrent inserts are not part of this page; an older stable page
	// remains valid even though unseen records were inserted after it.
	concurrent := exportSummary(validAuditEvent("audit-0", start.Add(30*time.Minute)))
	if err := (AuditEventPage{Items: []AuditEventSummary{older, concurrent}}).Validate(query, policy); err != nil {
		t.Fatalf("concurrent-insert page must remain valid: %v", err)
	}
}

// BLK-44087b945815: AuditEventDetailView anchored on the immutable event with
// request-context linkage, complete admission provenance, and complete
// credential-use evidence.

func TestAuditEventDetailRequiresAnchoredEvidenceChain(t *testing.T) {
	now := time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)
	base := validAuditEvent("audit-1", now)
	validDetail := AuditEventDetailView{
		Event:                 exportSummary(base),
		RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: "request-http-1", CausationID: base.CausationID, CorrelationID: base.CorrelationID},
		EvaluatedControls:     base.EvaluatedControls,
		authoritativeEvent:    &base,
		authorityDigest:       auditEventAuthorityDigest(base),
	}
	if err := validDetail.Validate(); err != nil {
		t.Fatalf("valid detail Validate() error = %v", err)
	}
	// Omitted request context.
	missing := validDetail
	missing.RequestContextSummary = RequestContextSummary{}
	if err := missing.Validate(); err == nil {
		t.Fatal("detail without redacted request context must be rejected")
	}
	// Mismatched request context linkage.
	mismatched := validDetail
	mismatched.RequestContextSummary.RequestContextID = "request-context-other"
	if err := mismatched.Validate(); err == nil {
		t.Fatal("detail with mismatched request context must be rejected")
	}
	// Missing evaluated controls.
	noControls := validDetail
	noControls.EvaluatedControls = nil
	if err := noControls.Validate(); err == nil {
		t.Fatal("detail without evaluated controls must be rejected")
	}
	// Retained secret material in a redaction-free credential use.
	noSecret := validDetail
	noSecret.CredentialUses = []CredentialUseEvidence{{BindingID: "binding-1", Fingerprint: "sha256:material-fingerprint", Purpose: CredentialPurposeAuditExport, Target: "target-1", Outcome: "succeeded", Availability: AvailabilityAvailable}}
	if err := noSecret.Validate(); err != nil {
		t.Fatalf("credential-use evidence Validate() error = %v", err)
	}
	// Missing fingerprint.
	noSecret.CredentialUses[0].Fingerprint = ""
	if err := noSecret.Validate(); err == nil {
		t.Fatal("credential use without fingerprint must be rejected")
	}
	// Missing target.
	noSecret.CredentialUses[0].Fingerprint = "sha256:material-fingerprint"
	noSecret.CredentialUses[0].Target = ""
	if err := noSecret.Validate(); err == nil {
		t.Fatal("credential use without target must be rejected")
	}
	// Invalid purpose.
	noSecret.CredentialUses[0].Target = "target-1"
	noSecret.CredentialUses[0].Purpose = CredentialPurpose("other")
	if err := noSecret.Validate(); err == nil {
		t.Fatal("credential use with unknown purpose must be rejected")
	}
}

func TestAdmissionControlProvenanceRequiresCompleteEvidence(t *testing.T) {
	now := time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)
	if err := (AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision"}).Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if err := (AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, DecisionDigest: "sha256:decision"}).Validate(); err == nil {
		t.Fatal("admission provenance without evaluated checks must be rejected")
	}
	if err := (AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionResult("abstain"), EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision"}).Validate(); err == nil {
		t.Fatal("admission provenance without a typed admit/reject result must be rejected")
	}
	if err := (AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckStatus("unknown-state"), Reason: "within limit"}}, DecisionDigest: "sha256:decision"}).Validate(); err == nil {
		t.Fatal("admission provenance with an invalid check status must be rejected")
	}
	if err := (AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision"}).Validate(); err != nil {
		t.Fatalf("typed check status must be accepted: %v", err)
	}
	// A detail with an admission control must carry the full provenance.
	nowTime := now
	event := validAuditEvent("audit-admission", nowTime)
	event.Category = CategoryAdmission
	event.EvaluatedControls = []EvaluatedControl{{Kind: ControlAdmission, ControlID: "admission-1", Revision: "1", Digest: "sha256:control", Result: "allowed", Admission: &AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision"}}}
	detail := AuditEventDetailView{Event: exportSummary(event), RequestContextSummary: RequestContextSummary{PrincipalID: event.PrincipalID, ScopeID: event.ScopeID, Source: "api", RequestContextID: event.RequestContextID, RequestID: event.RequestContextID, CausationID: event.CausationID, CorrelationID: event.CorrelationID}, EvaluatedControls: event.EvaluatedControls, authoritativeEvent: &event, authorityDigest: auditEventAuthorityDigest(event)}
	if err := detail.Validate(); err != nil {
		t.Fatalf("admission detail Validate() error = %v", err)
	}
	detail.EvaluatedControls[0].Admission.RulesDigest = ""
	if err := detail.Validate(); err == nil {
		t.Fatal("admission detail without rules digest must be rejected")
	}
}

func TestProviderControlProvenanceRequiresExactRelease(t *testing.T) {
	if err := (ProviderControlProvenance{ProviderID: "provider-1", Release: "provider@1.2.3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}).Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if err := (ProviderControlProvenance{ProviderID: "provider-1", ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}).Validate(); err == nil {
		t.Fatal("provider provenance without exact release must be rejected")
	}
	for _, release := range []string{"latest", "provider@1.2@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "provider@1.2.3@bad"} {
		if err := (ProviderControlProvenance{ProviderID: "provider-1", Release: release, ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}).Validate(); err == nil {
			t.Fatalf("provider provenance accepted malformed release %q", release)
		}
	}
	if err := (ProviderControlProvenance{ProviderID: "provider-1", Release: "provider@1.2.3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ResponseDigest: "sha256:response"}).Validate(); err == nil {
		t.Fatal("provider provenance accepted malformed response digest")
	}
}

func TestAuditEventPageEmptyAndNextCursorRules(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(24 * time.Hour)}, ProjectID: "project-1"}, PageSize: 2}
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: "next", binding: auditCursorBinding(request), position: CursorPosition{OccurredAt: start, RecordID: "audit-1"}}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	if err := (AuditEventPage{}).Validate(query, policy); err != nil {
		t.Fatalf("empty page Validate() error = %v", err)
	}
	unvalidated := query
	unvalidated.validated = false
	if err := (AuditEventPage{}).Validate(unvalidated, policy); err == nil {
		t.Fatal("empty page accepted an unvalidated query")
	}
	if err := (AuditEventPage{NextCursor: "next"}).Validate(query, policy); err == nil {
		t.Fatal("next cursor without items must be rejected")
	}
	// NextCursor from the validated page must go through the authenticated
	// final item (the same position the page validation checked).
	item := exportSummary(validAuditEvent("audit-1", start))
	item.ProjectID = "project-1"
	page := AuditEventPage{Items: []AuditEventSummary{item}, NextCursor: "next"}
	if err := page.Validate(query, policy); err != nil {
		t.Fatalf("linked page Validate() error = %v", err)
	}
	if got, err := query.NextCursor(page); err != nil || got != "next" {
		t.Fatalf("NextCursor() = %q, %v", got, err)
	}
	// NextCursor rejects a page whose final item is outside the filter even when structurally valid.
	item.ProjectID = "project-2"
	page.Items[0] = item
	page.NextCursor = ""
	if _, err := query.NextCursor(page); err == nil {
		t.Fatal("NextCursor() must reject an item outside the authenticated filter")
	}
}

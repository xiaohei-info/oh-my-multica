package audit

import (
	"context"
	"strings"
	"testing"
	"time"

	auditstore "github.com/xiaohei-info/open-agent-cluster/contracts/governance/audit/internal/auditstore"
)

func TestCanonicalExportFailureBranches(t *testing.T) {
	if _, err := CanonicalAuditExportPayload(ExportFormat("xml"), nil); err == nil {
		t.Fatal("unsupported export format was accepted")
	}
	if _, err := CanonicalAuditExportPayload(ExportFormatJSONL, []AuditExportEvent{{}}); err == nil {
		t.Fatal("invalid export event was serialized")
	}
	payload, err := CanonicalAuditExportPayload(ExportFormatCSV, nil)
	if err != nil || len(payload) == 0 {
		t.Fatalf("empty canonical CSV header failed: %v", err)
	}
}

func TestCanonicalExportPolicyAndEmptyChunkBranches(t *testing.T) {
	location := time.FixedZone("offset", 2*60*60)
	event := exportEvent(validAuditEvent("export-offset", time.Date(2026, time.August, 1, 14, 0, 0, 0, location)))
	jsonl, err := CanonicalAuditExportPayload(ExportFormatJSONL, []AuditExportEvent{event})
	if err != nil || !strings.Contains(string(jsonl), `"occurred_at":"2026-08-01T12:00:00Z"`) {
		t.Fatalf("JSONL timestamp was not normalized to UTC: %s (err=%v)", jsonl, err)
	}
	if err := (AuditExportStream{Format: ExportFormatCSV, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatCSV, nil)}}}).ValidateWithPolicy(DefaultExportPolicy()); err != nil {
		t.Fatalf("empty CSV chunk rejected: %v", err)
	}
	if err := (AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: nil}}}).ValidateWithPolicy(DefaultExportPolicy()); err != nil {
		t.Fatalf("empty JSONL chunk rejected: %v", err)
	}
	mixed := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: nil}, {Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{event}), Events: []AuditExportEvent{event}}}}
	if err := mixed.ValidateWithPolicy(DefaultExportPolicy()); err == nil {
		t.Fatal("empty export chunk was combined with event chunks")
	}
	tooManyRows := DefaultExportPolicy()
	tooManyRows.MaxRows = 1
	if err := (AuditExportStream{Format: ExportFormatJSONL, LimitExceeded: &ExportLimitExceeded{Limit: ExportLimitRows, Maximum: 2}}).ValidateWithPolicy(tooManyRows); err == nil {
		t.Fatal("row limit above policy was accepted")
	}
	tooManyBytes := DefaultExportPolicy()
	tooManyBytes.MaxBytes = 1
	if err := (AuditExportStream{Format: ExportFormatJSONL, LimitExceeded: &ExportLimitExceeded{Limit: ExportLimitBytes, Maximum: 2}}).ValidateWithPolicy(tooManyBytes); err == nil {
		t.Fatal("byte limit above policy was accepted")
	}
}

func TestStrictSemVerBoundaryBranches(t *testing.T) {
	for _, value := range []string{"1.2.3", "1.2.3-alpha.1", "1.2.3+build-1", "1.2.3-alpha+build"} {
		if !isStrictSemVer(value) {
			t.Fatalf("valid SemVer rejected: %q", value)
		}
	}
	for _, value := range []string{"", "v1.2.3", "1.2", "1.02.3", "1.2.3--alpha", "1.2.3-alpha-", "1.2.3-alpha..1", "1.2.3+", "1.2.3+build..1", "1.2.3+bad/value"} {
		if isStrictSemVer(value) {
			t.Fatalf("invalid SemVer accepted: %q", value)
		}
	}
}

func TestAuditStoreAndSaveCASFailureBranches(t *testing.T) {
	NewAuditEventStoreCapability(nil).auditEventStoreCapability()
	_ = TargetReplacementRequiredError{}.Error()
	_ = TargetReplacementRequiredError{Reason: "configuration"}.Error()
	if _, err := LoadAuditEventStoreBinding(context.Background(), nil, ""); err == nil {
		t.Fatal("missing store capability must fail")
	}
	if _, err := LoadAuditEventStoreBinding(context.Background(), NewAuditEventStoreCapability(nil), "event-1"); err == nil {
		t.Fatal("empty store loader must fail")
	}
	if _, err := LoadAuditEventStoreBinding(context.Background(), NewAuditEventStoreCapability(nil), "event-1"); err == nil {
		t.Fatal("caller-made binding must fail")
	}
	malformedStore := NewAuditEventStoreCapability(auditstore.NewReceipt("event-1", []byte("not-json")))
	if _, err := LoadAuditEventStoreBinding(context.Background(), malformedStore, "event-1"); err == nil {
		t.Fatal("malformed store receipt payload must fail")
	}
	if _, err := malformedStore.loadAuditEventBinding(nil, "event-1"); err == nil {
		t.Fatal("nil store context must fail")
	}
	event := validAuditEvent("event-1", time.Now().UTC())
	if _, err := issueAuditEventStoreBinding(event, nil); err == nil {
		t.Fatal("binding issuance without an authority proof must fail")
	}
	wrongEvent := event
	wrongEvent.ID = "other"
	wrongIDStore, err := signedAuditEventStore(wrongEvent)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := LoadAuditEventStoreBinding(context.Background(), wrongIDStore, "event-1"); err == nil {
		t.Fatal("store identity substitution must fail")
	}
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	save := SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), ExpectedRevision: "1", Draft: AuditExportTargetDraft{Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: configuration}}
	if err := save.ValidateWithPolicy(DefaultExportPolicy(), "target-1"); err == nil {
		t.Fatal("save update passed create-only policy validation")
	}
	digest, err := configuration.DigestFor(ExportTypeSyslog, "")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Revision: "1", ExportType: ExportTypeSyslog, Configuration: configuration, ConfigurationDigest: digest}
	if err := save.ValidateForTargetWithPolicy(DefaultExportPolicy(), target); err != nil {
		t.Fatalf("save update policy validation failed: %v", err)
	}
	create := save
	create.ExpectedRevision = ""
	if err := create.ValidateForTarget(target); err == nil {
		t.Fatal("save create was accepted by update CAS validation")
	}
}

func TestSafeSummaryClosedCatalogBranches(t *testing.T) {
	valid := SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "status", Value: SafeSummaryValueCompleted}}}
	if err := valid.Validate(); err != nil {
		t.Fatal(err)
	}
	cases := []SafeSummary{
		{Code: SafeSummaryCode("unknown")},
		{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "undeclared", Value: SafeSummaryValueCompleted}}},
		{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "status", Value: SafeSummaryValue("free-form")}}},
		{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "status", Value: SafeSummaryValueCompleted}, {Name: "status", Value: SafeSummaryValueUpdated}}},
	}
	for _, candidate := range cases {
		if err := candidate.Validate(); err == nil {
			t.Fatalf("invalid safe summary was accepted: %#v", candidate)
		}
	}
	many := SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: make([]SafeSummaryField, 13)}
	for index := range many.Fields {
		many.Fields[index] = SafeSummaryField{Name: "status", Value: SafeSummaryValueCompleted}
	}
	if err := many.Validate(); err == nil {
		t.Fatal("oversized safe summary field list was accepted")
	}
}

func TestAuditAuthorityAndChangeSummaryFailureBranches(t *testing.T) {
	event := validAuditEvent("audit-authority-coverage", time.Now().UTC())
	binding, err := issueTestAuditEventStoreBinding(event)
	if err != nil {
		t.Fatal(err)
	}
	if err := (AuditEventStoreBinding{}).Validate(); err == nil {
		t.Fatal("empty store binding must fail")
	}
	badBinding := binding
	badBinding.digest = "sha256:tampered"
	if err := badBinding.Validate(); err == nil {
		t.Fatal("tampered store binding must fail")
	}
	if _, err := NewAuditEventDetailViewFromStore(badBinding); err == nil {
		t.Fatal("detail construction must reject a tampered store binding")
	}
	bound, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatal(err)
	}
	bound.authoritativeEvent.ChangeSummary = &SafeSummary{Code: SafeSummaryCodeTargetLifecycle}
	if err := bound.Validate(); err == nil {
		t.Fatal("detail must reject an authority snapshot mutation")
	}
	for _, summary := range []string{string([]byte{0xff}), "line\nbreak", "state🙂"} {
		candidate := event
		candidate.ChangeSummary = &SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "reason", Value: SafeSummaryValue(summary)}}}
		if err := candidate.Validate(); err == nil {
			t.Fatalf("invalid summary %q must fail", summary)
		}
	}
}

func TestAuditFineGrainedValidationBranches(t *testing.T) {
	// EvaluatedControl provenance combinations.
	if err := (EvaluatedControl{Kind: ControlAuthorization, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed", Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant"}}).Validate(); err != nil {
		t.Fatalf("authorization Validate() error = %v", err)
	}
	if err := (EvaluatedControl{Kind: ControlAuthorization, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed"}).Validate(); err == nil {
		t.Fatal("authorization without provenance must fail")
	}
	if err := (EvaluatedControl{Kind: ControlAdmission, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: ControlResultDenied, Admission: &AdmissionControlProvenance{AdmissionID: "admission", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionReject, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckFail, Reason: "over limit"}}, DecisionDigest: "sha256:decision"}}).Validate(); err != nil {
		t.Fatalf("admission Validate() error = %v", err)
	}
	if err := (EvaluatedControl{Kind: ControlAdmission, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: ControlResultDenied, Admission: &AdmissionControlProvenance{AdmissionID: "admission", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionReject, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckFail, Reason: "over limit"}}, DecisionDigest: "sha256:decision"}}).Validate(); err != nil {
		t.Fatalf("reject admission Validate() error = %v", err)
	}
	badAdmission := EvaluatedControl{Kind: ControlAdmission, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed", Admission: &AdmissionControlProvenance{AdmissionID: "admission", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: nil, DecisionDigest: "sha256:decision"}}
	if err := badAdmission.Validate(); err == nil {
		t.Fatal("admission without evaluated checks must fail")
	}
	if err := (EvaluatedControl{Kind: ControlProvider, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed", Provider: &ProviderControlProvenance{ProviderID: "provider", Release: "provider@1.0.0@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}).Validate(); err != nil {
		t.Fatalf("provider Validate() error = %v", err)
	}
	if err := (EvaluatedControl{Kind: ControlProvider, ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed", Provider: &ProviderControlProvenance{ProviderID: "provider", ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}).Validate(); err == nil {
		t.Fatal("provider without release must fail")
	}
	// Audit event redaction and control validation branches.
	event := validAuditEvent("audit-1", time.Now().UTC())
	event.EvaluatedControls[0].Authorization = nil
	if err := event.Validate(); err == nil {
		t.Fatal("event with broken control must fail")
	}
	event = validAuditEvent("audit-1", time.Now().UTC())
	event.Redactions = []Redaction{{Field: "x", Reason: RedactionNotAuthorized}}
	if err := event.Validate(); err != nil {
		t.Fatalf("event with redaction Validate() error = %v", err)
	}
}

func TestAuditSummaryAndPageValidationBranches(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	summary := exportSummary(validAuditEvent("audit-1", start))
	summary.Redactions = []Redaction{{Field: "x", Reason: RedactionRetention}}
	if err := summary.Validate(); err != nil {
		t.Fatalf("summary with redaction Validate() error = %v", err)
	}
	summary.Redactions[0].Reason = "bad"
	if err := summary.Validate(); err == nil {
		t.Fatal("summary with bad redaction must fail")
	}

	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(24 * time.Hour)}}, PageSize: 2}
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: "next", binding: auditCursorBinding(request), position: CursorPosition{OccurredAt: start, RecordID: "audit-1"}}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	item := exportSummary(validAuditEvent("audit-1", start))
	if err := (AuditEventPage{Items: []AuditEventSummary{item}, NextCursor: "next"}).Validate(query, policy); err != nil {
		t.Fatalf("page Validate() error = %v", err)
	}
	// Invalid policy.
	if err := (AuditEventPage{Items: []AuditEventSummary{item}}).Validate(query, QueryPolicy{}); err == nil {
		t.Fatal("page with invalid policy must fail")
	}
	// Malformed item.
	broken := item
	broken.ID = ""
	if err := (AuditEventPage{Items: []AuditEventSummary{broken}}).Validate(query, policy); err == nil {
		t.Fatal("page with malformed item must fail")
	}
	// Request context accessors.
	if query.Binding() != auditCursorBinding(request) {
		t.Fatal("Binding() must expose the validated binding")
	}
	if query.CursorCodec() == nil {
		t.Fatal("CursorCodec() must expose the validated codec")
	}
	// Seek on a first-page query returns nil.
	first := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, PageSize: 1}
	firstQuery, err := first.ValidatedWithPolicy(DefaultQueryPolicy())
	if err != nil {
		t.Fatal(err)
	}
	if firstQuery.Seek() != nil {
		t.Fatal("first-page query must have a nil seek")
	}
	// NextCursor rejects a structurally invalid final item.
	if _, err := query.NextCursor(AuditEventPage{Items: []AuditEventSummary{{}}}); err == nil {
		t.Fatal("NextCursor() must reject a malformed final item")
	}
	// ValidatedWithPolicy rejects an invalid filter.
	bad := request
	bad.Filter.Category = "made-up"
	if _, err := bad.ValidatedWithPolicy(policy); err == nil {
		t.Fatal("ValidatedWithPolicy() must reject an invalid filter")
	}
}

func TestAuditFilterAcceptsEveryBoundDimension(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	item := exportSummary(validAuditEvent("audit-1", start))
	item.ProjectID = "project-1"
	item.Category = CategoryAuthorization
	item.Action = "Authorize"
	item.Outcome = OutcomeAllowed
	item.PrincipalID = "principal-1"
	item.Subject = SubjectKey{Kind: "project", ID: "project-1"}
	item.CorrelationID = "correlation-1"

	tests := []struct {
		name   string
		filter AuditEventFilter
		want   bool
	}{
		{name: "time-window", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}}, want: true},
		{name: "outside-time", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-2 * time.Hour), End: start.Add(-time.Hour)}}, want: false},
		{name: "project", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, ProjectID: "project-1"}, want: true},
		{name: "project-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, ProjectID: "project-2"}, want: false},
		{name: "category", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Category: CategoryAuthorization}, want: true},
		{name: "category-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Category: CategoryAdmission}, want: false},
		{name: "action", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Action: "Authorize"}, want: true},
		{name: "action-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Action: "Other"}, want: false},
		{name: "outcome", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Outcome: OutcomeAllowed}, want: true},
		{name: "outcome-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Outcome: OutcomeDenied}, want: false},
		{name: "principal", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, PrincipalID: "principal-1"}, want: true},
		{name: "principal-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, PrincipalID: "principal-2"}, want: false},
		{name: "subject", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Subject: &SubjectKey{Kind: "project", ID: "project-1"}}, want: true},
		{name: "subject-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, Subject: &SubjectKey{Kind: "project", ID: "project-2"}}, want: false},
		{name: "correlation", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, CorrelationID: "correlation-1"}, want: true},
		{name: "correlation-mismatch", filter: AuditEventFilter{TimeRange: TimeRange{Start: start.Add(-time.Hour), End: start.Add(time.Hour)}, CorrelationID: "correlation-2"}, want: false},
	}
	for _, test := range tests {
		if got := filterAccepts(test.filter, item); got != test.want {
			t.Fatalf("filterAccepts(%s) = %v, want %v", test.name, got, test.want)
		}
	}
	// precedesAuditSummary tie-break by id.
	left := exportSummary(validAuditEvent("audit-2", start))
	right := exportSummary(validAuditEvent("audit-1", start))
	if !precedesAuditSummary(left, right) || precedesAuditSummary(right, left) {
		t.Fatal("precedesAuditSummary must break ties by descending id")
	}
}

func TestAuditProjectionAndAvailabilityBranchCoverage(t *testing.T) {
	if _, err := (AuditEvent{}).Summary(); err == nil {
		t.Fatal("invalid event Summary() must fail")
	}
	if err := (AuditEventDetailView{}).Validate(); err == nil {
		t.Fatal("detail without an authoritative event must fail")
	}
	event := validAuditEvent("audit-provider", time.Now().UTC())
	event.EvaluatedControls = append(event.EvaluatedControls, EvaluatedControl{Kind: ControlProvider, ControlID: "provider", Revision: "1", Digest: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Result: ControlResultAllowed, Provider: &ProviderControlProvenance{ProviderID: "provider", Release: "provider@1.0.0@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", ResponseDigest: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}})
	if _, err := event.Summary(); err != nil {
		t.Fatalf("provider event Summary() error = %v", err)
	}
	base := validAuditEvent("audit-availability", time.Now().UTC())
	makeDetail := func(record RelatedGovernanceRecord) AuditEventDetailView {
		return AuditEventDetailView{Event: exportSummary(base), RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: base.RequestContextID, CausationID: base.CausationID, CorrelationID: base.CorrelationID}, EvaluatedControls: base.EvaluatedControls, RelatedGovernanceRecords: []RelatedGovernanceRecord{record}, authoritativeEvent: &base, authorityDigest: auditEventAuthorityDigest(base)}
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityAvailable}).Validate(); err != nil {
		t.Fatalf("available related record rejected: %v", err)
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityAvailable, Reason: AvailabilityReasonMissing}).Validate(); err == nil {
		t.Fatal("available related record with a reason must fail")
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityUnavailable, Reason: AvailabilityReasonMissing}).Validate(); err != nil {
		t.Fatalf("unavailable related record rejected: %v", err)
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityUnavailable, Reason: AvailabilityReasonNotAuthorized}).Validate(); err == nil {
		t.Fatal("unavailable related record with an authorization reason must fail")
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityUnavailable, Reason: AvailabilityReasonMissing, Redactions: []Redaction{{Field: "request", Reason: RedactionRetention}}}).Validate(); err == nil {
		t.Fatal("unavailable related record with field redactions must fail")
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityRedacted, Reason: AvailabilityReasonRetention}).Validate(); err == nil {
		t.Fatal("redacted related record without field redactions must fail")
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityRedacted, Reason: AvailabilityReasonRetention, Redactions: []Redaction{{Field: "request", Reason: RedactionRetention}}}).Validate(); err != nil {
		t.Fatalf("redacted related record rejected: %v", err)
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityNotAuthorized, Reason: AvailabilityReasonNotAuthorized}).Validate(); err == nil {
		t.Fatal("not-authorized related record without field redactions must fail")
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityNotAuthorized, Reason: AvailabilityReasonNotAuthorized, Redactions: []Redaction{{Field: "request", Reason: RedactionNotAuthorized}}}).Validate(); err != nil {
		t.Fatalf("not-authorized related record rejected: %v", err)
	}
	if err := makeDetail(RelatedGovernanceRecord{Kind: "request", ID: "request-1", Availability: AvailabilityRedacted, Reason: AvailabilityReason("other"), Redactions: []Redaction{{Field: "request", Reason: RedactionRetention}}}).Validate(); err == nil {
		t.Fatal("related record with an unknown availability reason must fail")
	}
}

func TestAuditDetailViewFineGrainedValidation(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	base := validAuditEvent("audit-1", start)
	valid := AuditEventDetailView{Event: exportSummary(base), RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: base.RequestContextID, CausationID: base.CausationID, CorrelationID: base.CorrelationID}, EvaluatedControls: base.EvaluatedControls, authoritativeEvent: &base, authorityDigest: auditEventAuthorityDigest(base)}
	if err := valid.Validate(); err != nil {
		t.Fatalf("valid detail Validate() error = %v", err)
	}
	// Related governance record redaction.
	badRecord := valid
	badRecord.RelatedGovernanceRecords = []RelatedGovernanceRecord{{Kind: "request", ID: "request-1", Availability: AvailabilityAvailable, Redactions: []Redaction{{Field: "", Reason: RedactionSecret}}}}
	if err := badRecord.Validate(); err == nil {
		t.Fatal("detail with malformed related record redaction must fail")
	}
	// Related subject validation.
	badSubject := valid
	badSubject.RelatedSubjects = []RelatedSubject{{Subject: SubjectKey{Kind: "project", ID: "project-1"}, Availability: AvailabilityUnavailable, Reason: AvailabilityReasonMissing}}
	if err := badSubject.Validate(); err != nil {
		t.Fatalf("available related subject Validate() error = %v", err)
	}
	badSubject.RelatedSubjects[0].Availability = "other"
	if err := badSubject.Validate(); err == nil {
		t.Fatal("detail with invalid related subject availability must fail")
	}
	// Credential use redaction.
	badUse := valid
	badUse.CredentialUses = []CredentialUseEvidence{{Purpose: CredentialPurposeMCPServer, Availability: AvailabilityRedacted, Redactions: []Redaction{{Field: "binding_id", Reason: RedactionRetention}, {Field: "fingerprint", Reason: RedactionRetention}, {Field: "target", Reason: RedactionRetention}, {Field: "outcome", Reason: RedactionRetention}}}}
	if err := badUse.Validate(); err != nil {
		t.Fatalf("redacted credential use Validate() error = %v", err)
	}
	// Top-level detail redaction.
	badRedaction := valid
	badRedaction.Redactions = []Redaction{{Field: "", Reason: RedactionSecret}}
	if err := badRedaction.Validate(); err == nil {
		t.Fatal("detail with malformed top-level redaction must fail")
	}
	// Request context summary validation.
	if err := (RequestContextSummary{PrincipalID: "p", ScopeID: "s", Source: "api", RequestContextID: "context", RequestID: "r", CausationID: "cause", CorrelationID: "corr"}).Validate(); err != nil {
		t.Fatalf("request context Validate() error = %v", err)
	}
	if err := (RequestContextSummary{ScopeID: "s", Source: "api", RequestID: "r"}).Validate(); err == nil {
		t.Fatal("request context without principal must fail")
	}
}

func TestAuditExportRequestAndTargetValidationBranches(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	// Export request: invalid subject filter.
	if err := (ExportAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}, Subject: &SubjectKey{Kind: "project"}}, Format: ExportFormatCSV}).Validate(); err == nil {
		t.Fatal("export with invalid subject filter must fail")
	}
	if err := (ExportAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, Format: ExportFormatCSV}).ValidateWithPolicy(QueryPolicy{}, DefaultExportPolicy()); err == nil {
		t.Fatal("export with invalid query policy must fail")
	}

	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	// Configuration.ValidateFor unknown type.
	if err := configuration.ValidateFor(ExportType("made-up")); err == nil {
		t.Fatal("configuration for unknown type must fail")
	}

	// Target validation branches.
	if err := (AuditExportTarget{}).Validate(); err == nil {
		t.Fatal("empty target must fail")
	}
	digest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: "binding-1", Revision: "1"}
	if err := target.Validate(); err != nil {
		t.Fatalf("target Validate() error = %v", err)
	}
	target.ConfigurationDigest = "wrong"
	if err := target.Validate(); err == nil {
		t.Fatal("target with mismatched digest must fail")
	}
	target.ConfigurationDigest = digest
	// Candidate validation branches are covered by the public draft lifecycle tests below.
	// Candidate without binding for a non-authenticating type is valid and
	// the policy path returns early without a resolver.
	syslogConfiguration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	syslogCandidate := AuditExportTargetDraft{Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: syslogConfiguration}
	if err := syslogCandidate.ValidateWithPolicy(ExportPolicy{}, "target-2", "scope-1"); err != nil {
		t.Fatalf("syslog candidate ValidateWithPolicy() error = %v", err)
	}
}

func TestAuditExportLifecycleRequestValidation(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	candidate := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	binding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "target-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	policy := ExportPolicy{MaxRows: 10, MaxBytes: 1024, CredentialBindings: auditBindingResolver{binding: binding}}

	// Save request validation branches.
	if err := (SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate}).Validate(); err != nil {
		t.Fatalf("save Validate() error = %v", err)
	}
	if err := (SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1")}).Validate(); err == nil {
		t.Fatal("save with empty candidate must fail")
	}
	if err := (SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate}).ValidateWithPolicy(policy, "target-1"); err != nil {
		t.Fatalf("save ValidateWithPolicy() error = %v", err)
	}
	if err := (SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1")}).ValidateWithPolicy(policy, "target-1"); err == nil {
		t.Fatal("save with empty candidate must fail under policy")
	}
	// Disable request branch.
	if err := (DisableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "target-1", ExpectedRevision: "1", ConfigurationDigest: "sha256:config"}).Validate(); err != nil {
		t.Fatalf("disable Validate() error = %v", err)
	}
	// Replace request branches.
	replacementConfiguration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	replacement := AuditExportTargetDraft{Name: "syslog", Description: "replacement", ExportType: ExportTypeSyslog, Configuration: replacementConfiguration}
	targetDigest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: targetDigest, CredentialBindingID: "binding-1", Revision: "1"}
	replace := ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), CurrentTargetID: "target-1", ExpectedRevision: "1", Replacement: replacement}
	if err := replace.Validate(); err != nil {
		t.Fatalf("replace Validate() error = %v", err)
	}
	if err := replace.ValidateWithPolicy(policy, "target-2", target); err != nil {
		t.Fatalf("replace ValidateWithPolicy() error = %v", err)
	}
	// Probe request branches.
	probe := AuditExportProbeRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate, Payload: AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "x", Reason: RedactionSecret}}}, Timeout: time.Second}
	if err := probe.Validate(); err != nil {
		t.Fatalf("probe Validate() error = %v", err)
	}
	if err := probe.ValidateWithPolicy(policy, "target-1"); err != nil {
		t.Fatalf("probe ValidateWithPolicy() error = %v", err)
	}
	probe.Payload.Redactions = nil
	if err := probe.Validate(); err == nil {
		t.Fatal("probe without redactions must fail")
	}
	// Probe with a syslog, non-authenticating candidate under policy.
	syslogProbe := AuditExportProbeRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: replacement, Payload: AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "x", Reason: RedactionSecret}}}, Timeout: time.Second}
	if err := syslogProbe.ValidateWithPolicy(ExportPolicy{MaxRows: 10, MaxBytes: 1024}, "target-2"); err != nil {
		t.Fatalf("syslog probe ValidateWithPolicy() error = %v", err)
	}
}

func TestAuditExportBatchAndAcknowledgementValidationBranches(t *testing.T) {
	event := exportEvent(validAuditEvent("audit-1", time.Date(2026, time.July, 31, 0, 0, 0, 0, time.UTC)))
	// Batch with malformed event.
	if err := (AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: "start", EndCursor: "end", Events: []AuditExportEvent{{}}, CursorInterval: []string{"end"}}).Validate(); err == nil {
		t.Fatal("batch with malformed event must fail")
	}
	// Batch with out-of-order events.
	older := exportEvent(validAuditEvent("audit-0", time.Date(2026, time.July, 30, 0, 0, 0, 0, time.UTC)))
	if err := (AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: "c0", EndCursor: "c1", Events: []AuditExportEvent{older, event}, CursorInterval: []string{"c0", "c1"}}).Validate(); err == nil {
		t.Fatal("batch with out-of-order events must fail")
	}
	// Batch with unbounded cursors.
	if err := (AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: event.Cursor, EndCursor: event.Cursor, Events: []AuditExportEvent{event}, CursorInterval: []string{"other"}}).Validate(); err == nil {
		t.Fatal("batch with unbounded cursor interval must fail")
	}
	// Acknowledgement outside the interval.
	batch := AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: event.Cursor, EndCursor: event.Cursor, Events: []AuditExportEvent{event}, CursorInterval: []string{event.Cursor}}
	if err := (AuditExportAcknowledgement{TargetID: "target-1", DeliveryID: "delivery-1", StartCursor: event.Cursor, HighestContiguousCursor: event.Cursor}).ValidateFor(batch); err != nil {
		t.Fatalf("acknowledgement ValidateFor() error = %v", err)
	}
	if err := (AuditExportAcknowledgement{TargetID: "target-1", DeliveryID: "delivery-1", StartCursor: event.Cursor, HighestContiguousCursor: "missing"}).ValidateFor(batch); err == nil {
		t.Fatal("acknowledgement outside the delivered interval must fail")
	}
	if err := (AuditExportAcknowledgement{}).ValidateFor(AuditExportBatch{}); err == nil {
		t.Fatal("acknowledgement for an invalid batch must fail")
	}
}

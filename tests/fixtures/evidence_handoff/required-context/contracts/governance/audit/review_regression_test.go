package audit

import (
	"errors"
	"testing"
	"time"
)

type auditCredentialBindingResolverFunc func(string) (CredentialBinding, error)

func (resolver auditCredentialBindingResolverFunc) ResolveCredentialBinding(id string) (CredentialBinding, error) {
	return resolver(id)
}

func TestAuditExportCredentialResolutionBindsRequestedIdentityAcrossLifecycle(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	binding := func(id, subjectID string) CredentialBinding {
		return CredentialBinding{ID: id, ScopeID: "scope-1", SubjectID: subjectID, State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	}
	policy := func(requestedID, returnedID, subjectID string, resolverErr error) ExportPolicy {
		return ExportPolicy{CredentialBindings: auditCredentialBindingResolverFunc(func(id string) (CredentialBinding, error) {
			if id != requestedID {
				return CredentialBinding{}, errors.New("unexpected credential binding lookup")
			}
			if resolverErr != nil {
				return CredentialBinding{}, resolverErr
			}
			return binding(returnedID, subjectID), nil
		})}
	}
	draft := func(id, name string) AuditExportTargetDraft {
		return AuditExportTargetDraft{Name: name, Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: id}
	}
	target := func(id, targetID string) AuditExportTarget {
		digest, err := configuration.DigestFor(ExportTypeWebhook, id)
		if err != nil {
			t.Fatal(err)
		}
		return AuditExportTarget{ID: targetID, ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: id, Revision: "7"}
	}
	current := target("current-binding", "current-target")
	requestContext := testRequestContext("scope-1", "request-1")
	createDraft := draft("create-binding", "create")
	updateTarget := target("update-binding", "update-target")
	update := SaveAuditExportTargetRequest{RequestContext: requestContext, Draft: draft("update-binding", "update"), ExpectedRevision: updateTarget.Revision}
	enableTarget := target("enable-binding", "enable-target")
	enable := EnableAuditExportTargetRequest{RequestContext: requestContext, TargetID: enableTarget.ID, ExpectedRevision: enableTarget.Revision, ConfigurationDigest: enableTarget.ConfigurationDigest}
	replacement := ReplaceAuditExportTargetRequest{RequestContext: requestContext, CurrentTargetID: current.ID, ExpectedRevision: current.Revision, Replacement: draft("replacement-binding", "replacement")}

	cases := []struct {
		name        string
		requestedID string
		targetID    string
		validate    func(ExportPolicy) error
	}{
		{"create", "create-binding", "new-target", func(p ExportPolicy) error { return createDraft.ValidateWithPolicy(p, "new-target", "scope-1") }},
		{"update", "update-binding", updateTarget.ID, func(p ExportPolicy) error { return update.ValidateForTargetWithPolicy(p, updateTarget) }},
		{"enable", "enable-binding", enableTarget.ID, func(p ExportPolicy) error {
			if err := enableTarget.ValidateWithPolicy(p); err != nil {
				return err
			}
			return enable.ValidateForTarget(enableTarget)
		}},
		{"replace", "replacement-binding", "replacement-target", func(p ExportPolicy) error { return replacement.ValidateWithPolicy(p, "replacement-target", current) }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if err := test.validate(policy(test.requestedID, "resolver-returned-foreign-binding", test.targetID, nil)); err == nil {
				t.Fatal("credential metadata with a different resolved identity authorized the operation")
			}
			if err := test.validate(policy(test.requestedID, test.requestedID, test.targetID, nil)); err != nil {
				t.Fatalf("correctly bound credential was rejected: %v", err)
			}
		})
	}
	resolverErr := errors.New("credential resolver unavailable")
	if err := createDraft.ValidateWithPolicy(policy("create-binding", "", "", resolverErr), "new-target", "scope-1"); !errors.Is(err, resolverErr) {
		t.Fatalf("credential resolver error = %v, want preserved error", err)
	}
}

func TestAuditExportTargetAndProbeRequireSafeInlineCandidate(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	candidate := AuditExportTargetDraft{
		Name:          "siem",
		Description:   "security event endpoint",
		ExportType:    "webhook",
		Configuration: configuration, CredentialBindingID: "binding-1",
	}
	request := AuditExportProbeRequest{
		RequestContext: testRequestContext("scope-1", "request-1"),
		Draft:          candidate,
		Payload:        AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "authorization", Reason: RedactionSecret}}},
		Timeout:        time.Second,
	}
	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if err := (AuditExportProbeRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate, Payload: request.Payload, Timeout: 0}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected missing timeout")
	}
}

func TestAuditContractsRejectMissingEvidenceAndInvalidDetailAvailability(t *testing.T) {
	event := validAuditEvent("audit-1", time.Date(2026, time.July, 30, 12, 0, 0, 0, time.UTC))
	event.EvaluatedControls = nil
	if err := event.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected missing evaluated controls")
	}

	detailEvent := validAuditEvent("audit-1", time.Date(2026, time.July, 30, 12, 0, 0, 0, time.UTC))
	detail := AuditEventDetailView{
		Event:                 exportSummary(detailEvent),
		RequestContextSummary: RequestContextSummary{PrincipalID: "principal-1", ScopeID: "scope-1", Source: "api", RequestContextID: "request-1", RequestID: "request-http-1", CausationID: "cause-audit-1", CorrelationID: "corr-audit-1"},
		authoritativeEvent:    &detailEvent,
		authorityDigest:       auditEventAuthorityDigest(detailEvent),
		EvaluatedControls:     []EvaluatedControl{{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "1", Digest: "sha256:policy", Result: ControlResultAllowed, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"}}},
		RelatedGovernanceRecords: []RelatedGovernanceRecord{{
			Kind:         "credential-use",
			ID:           "credential-use-1",
			Availability: "unknown",
		}},
	}
	if err := detail.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected untyped availability")
	}
}

func TestQueryAuditEventsRejectsForgedCursorAndPolicyViolations(t *testing.T) {
	start := time.Date(2026, time.July, 1, 0, 0, 0, 0, time.UTC)
	request := QueryAuditEventsRequest{
		RequestContext: testRequestContext("scope-1", "request-1"),
		Filter:         AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(24 * time.Hour)}},
		Cursor:         "",
		PageSize:       DefaultQueryPolicy().MaxPageSize,
	}
	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	request.Cursor = "server-issued-audit-cursor"
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: request.Cursor, binding: auditCursorBinding(request), position: CursorPosition{OccurredAt: start, RecordID: "audit-1"}}
	if err := request.ValidateWithPolicy(policy); err != nil {
		t.Fatalf("ValidateWithPolicy() error = %v", err)
	}
	request.RequestContext.ScopeID = "scope-2"
	if err := request.ValidateWithPolicy(policy); err == nil {
		t.Fatal("ValidateWithPolicy() error = nil, want rejected cross-scope cursor replay")
	}
	request.RequestContext.ScopeID = "scope-1"
	request.Filter.ProjectID = "project-2"
	if err := request.ValidateWithPolicy(policy); err == nil {
		t.Fatal("ValidateWithPolicy() error = nil, want rejected cross-filter cursor replay")
	}
	request.Filter.ProjectID = ""
	request.Cursor = "forged-unvalidated-cursor"
	if err := request.ValidateWithPolicy(policy); err == nil {
		t.Fatal("ValidateWithPolicy() error = nil, want rejected forged cursor")
	}
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want resumed page rejected without server codec")
	}
	request.Cursor = "server-issued-audit-cursor"
	policy.CursorCodec = auditTestCursorCodec{token: request.Cursor, binding: auditCursorBinding(request)}
	if err := request.ValidateWithPolicy(policy); err == nil {
		t.Fatal("ValidateWithPolicy() error = nil, want cursor codec without an authenticated position rejected")
	}
	request.Cursor = ""
	request.PageSize = DefaultQueryPolicy().MaxPageSize + 1
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected oversized page")
	}
	request.PageSize = 1
	request.Filter.TimeRange.End = start.Add(DefaultQueryPolicy().MaxTimeRange + time.Nanosecond)
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected unbounded time range")
	}
}

func TestAuditExportContractsValidateTypedFailureBoundaries(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: "endpoint", Value: "https://siem.example.test/events"}}}
	digest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "security event endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: "binding-1", Revision: "1"}
	if err := target.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if err := (AuditExportConfiguration{Fields: []ConfigurationField{{Name: "endpoint", Value: "one"}, {Name: "endpoint", Value: "two"}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected duplicate configuration field")
	}
	if err := (AuditExportTargetDraft{}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected incomplete candidate")
	}
	if err := (AuditExportProbePayload{}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected empty probe payload")
	}
	if err := (AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "secret", Reason: "unknown"}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected invalid probe redaction")
	}
	if err := (AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: "authorization_header", Value: "Bearer secret-token"}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected secret-like configuration")
	}
	if err := (AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportType("made-up"), Configuration: configuration}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected unknown export type")
	}
	if err := (AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected mismatched type and schema")
	}
	for _, endpoint := range []string{"https://user:abc123@siem.example.test/events", "https://siem.example.test/events?token=abc123"} {
		if err := (AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: endpoint}}}}).Validate(); err == nil {
			t.Fatalf("Validate() error = nil, want rejected sensitive endpoint %q", endpoint)
		}
	}
	if err := (AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe", Fields: []SafeSummaryField{{Name: "reason", Value: "Bearer secret-token"}}}, Redactions: []Redaction{{Field: "authorization", Reason: RedactionSecret}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected secret-like probe summary")
	}
	if err := (AuditExportProbeResult{Outcome: AuditExportProbeSucceeded, FailureCode: ProbeFailureTimeout}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected inconsistent successful result")
	}
	if err := (AuditExportProbeResult{Outcome: AuditExportProbeFailed, FailureCode: ProbeFailureCode("external-status-503"), Redactions: []Redaction{{Field: "authorization", Reason: RedactionSecret}}}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected arbitrary external failure")
	}
	if err := (AuditExportProbeResult{Outcome: AuditExportProbeFailed, FailureCode: ProbeFailureTimeout, Redactions: []Redaction{{Field: "authorization", Reason: RedactionSecret}}}).Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestAuditDetailAndCursorValidationCoverStructuredReasons(t *testing.T) {
	detailEvent := validAuditEvent("audit-1", time.Date(2026, time.July, 30, 12, 0, 0, 0, time.UTC))
	detail := AuditEventDetailView{
		Event:                 exportSummary(detailEvent),
		RequestContextSummary: RequestContextSummary{PrincipalID: "principal-1", ScopeID: "scope-1", Source: "api", RequestContextID: "request-1", RequestID: "request-http-1", CausationID: "cause-audit-1", CorrelationID: "corr-audit-1"},
		authoritativeEvent:    &detailEvent,
		authorityDigest:       auditEventAuthorityDigest(detailEvent),
		EvaluatedControls:     []EvaluatedControl{{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "1", Digest: "sha256:policy", Result: ControlResultAllowed, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"}}},
		RelatedGovernanceRecords: []RelatedGovernanceRecord{{
			Kind:         "credential-use",
			ID:           "credential-use-1",
			Availability: AvailabilityUnavailable,
			Reason:       AvailabilityReasonRetention,
		}},
	}
	if err := detail.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	binding, err := cursorBinding("scope-1", AuditEventFilter{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := validateCursor("forged-cursor", binding, nil); err == nil {
		t.Fatal("validateCursor() error = nil, want rejected cursor without deployment codec")
	}
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	left := AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}, ProjectID: "alpha~beta", Action: "gamma"}
	right := AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}, ProjectID: "alpha", Action: "beta~gamma"}
	leftFingerprint, err := filterFingerprint(left)
	if err != nil {
		t.Fatal(err)
	}
	rightFingerprint, err := filterFingerprint(right)
	if err != nil {
		t.Fatal(err)
	}
	if leftFingerprint == rightFingerprint {
		t.Fatal("filterFingerprint() collision for distinct delimiter-containing filters")
	}
}

func TestAuditExportPoliciesCredentialAndRedactedDTOs(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	binding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "target-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	candidate := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	if err := candidate.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: binding}}, "target-1", "scope-1"); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	binding.ScopeID = "scope-2"
	if err := candidate.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: binding}}, "target-1", "scope-1"); err == nil {
		t.Fatal("Validate() error = nil, want rejected out-of-scope credential")
	}
	binding.ScopeID = "scope-1"
	if err := (AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration}).Validate(); err == nil {
		t.Fatal("draft without required credential binding must fail")
	}
	queriedEndpoint := AuditExportTargetDraft{Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook,
		Configuration: AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events?region=us"}}},
	}
	if err := queriedEndpoint.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected endpoint query")
	}

	query := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, PageSize: 1}
	request := ExportAuditEventsRequest{RequestContext: query.RequestContext, Filter: query.Filter, Format: ExportFormatJSONL}
	if err := request.ValidateWithPolicy(DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("ValidateWithPolicy() error = %v", err)
	}
	if err := request.ValidateWithPolicy(DefaultQueryPolicy(), ExportPolicy{}); err == nil {
		t.Fatal("ValidateWithPolicy() error = nil, want invalid export policy")
	}

	event := exportEvent(validAuditEvent("audit-1", start))
	if err := event.Validate(); err != nil {
		t.Fatalf("export event Validate() error = %v", err)
	}
	if err := exportSummary(validAuditEvent("audit-1", start)).Validate(); err != nil {
		t.Fatalf("summary Validate() error = %v", err)
	}
	payload := exportPayload(ExportFormatJSONL, []AuditExportEvent{event})
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: payload, Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 1, MaxBytes: len(payload)}); err != nil {
		t.Fatalf("stream ValidateWithPolicy() error = %v", err)
	}
	stream.Chunks[0].Payload = []byte("event-data-extended")
	if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 1, MaxBytes: 10}); err == nil {
		t.Fatal("stream error = nil, want byte limit")
	}
	stream = AuditExportStream{Format: ExportFormatJSONL, LimitExceeded: &ExportLimitExceeded{Limit: ExportLimitBytes, Maximum: 10}}
	if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 1, MaxBytes: 10}); err != nil {
		t.Fatalf("typed no-chunk limit error = %v", err)
	}
	if err := (ExportLimitExceeded{}).Validate(); err == nil {
		t.Fatal("limit Validate() error = nil, want invalid limit")
	}
}

type auditTestCursorCodec struct {
	token    string
	binding  CursorBinding
	position CursorPosition
}

func (codec auditTestCursorCodec) ValidateCursor(cursor string, binding CursorBinding) (CursorPosition, error) {
	if cursor != codec.token || binding != codec.binding {
		return CursorPosition{}, errors.New("cursor was not issued for this query")
	}
	return codec.position, nil
}

func (codec auditTestCursorCodec) IssueCursor(binding CursorBinding, position CursorPosition) (string, error) {
	if binding != codec.binding || position != codec.position {
		return "", errors.New("cursor issued for wrong query")
	}
	return codec.token, nil
}

func auditCursorBinding(request QueryAuditEventsRequest) CursorBinding {
	binding, err := cursorBinding(request.RequestContext.ScopeID, request.Filter)
	if err != nil {
		panic(err)
	}
	return binding
}

type auditBindingResolver struct{ binding CredentialBinding }

func (resolver auditBindingResolver) ResolveCredentialBinding(id string) (CredentialBinding, error) {
	if id != resolver.binding.ID {
		return CredentialBinding{}, errors.New("binding not found")
	}
	return resolver.binding, nil
}

func TestAuditCursorIssuanceUsesCanonicalValidatedBinding(t *testing.T) {
	start := time.Date(2026, time.July, 31, 9, 0, 0, 0, time.FixedZone("offset", 8*60*60))
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}}, PageSize: 1}
	binding, err := cursorBinding(request.RequestContext.ScopeID, request.Filter)
	if err != nil {
		t.Fatal(err)
	}
	position := CursorPosition{OccurredAt: start.UTC(), RecordID: "audit-1"}
	policy := DefaultQueryPolicy()
	policy.CursorCodec = auditTestCursorCodec{token: "issued", binding: binding, position: position}
	query, err := request.ValidatedWithPolicy(policy)
	if err != nil {
		t.Fatal(err)
	}
	item := exportSummary(validAuditEvent("audit-1", start))
	page := AuditEventPage{Items: []AuditEventSummary{item}}
	if got, err := query.NextCursor(page); err != nil || got != "issued" {
		t.Fatalf("NextCursor() = %q, %v", got, err)
	}
	if _, err := (ValidatedAuditEventsQuery{}).NextCursor(page); err == nil {
		t.Fatal("expected unconfigured issuer to fail")
	}
	wrong := item
	wrong.ScopeID = "scope-2"
	page.Items[0] = wrong
	if _, err := query.NextCursor(page); err == nil {
		t.Fatal("expected cross-scope cursor issuance to fail")
	}
	equivalent := request.Filter
	equivalent.TimeRange.Start = request.Filter.TimeRange.Start.UTC()
	equivalent.TimeRange.End = request.Filter.TimeRange.End.UTC()
	left, err := filterFingerprint(request.Filter)
	if err != nil {
		t.Fatal(err)
	}
	right, err := filterFingerprint(equivalent)
	if err != nil {
		t.Fatal(err)
	}
	if left != right {
		t.Fatal("equivalent instants must have canonical cursor binding")
	}
	if left != `{"Start":"2026-07-31T01:00:00Z","End":"2026-07-31T02:00:00Z","ProjectID":"","Category":"","Action":"","Outcome":"","PrincipalID":"","Subject":null,"CorrelationID":""}` {
		t.Fatalf("unexpected canonical audit cursor binding: %s", left)
	}
	original := marshalCursorFilter
	marshalCursorFilter = func(any) ([]byte, error) { return nil, errors.New("marshal failed") }
	defer func() { marshalCursorFilter = original }()
	if _, err := filterFingerprint(request.Filter); err == nil {
		t.Fatal("expected cursor serialization error to propagate")
	}
}

func TestAuditExportHardeningContracts(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldTenant, Value: "tenant-1"}, {Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	digest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	reordered := AuditExportConfiguration{SchemaVersion: configuration.SchemaVersion, Fields: []ConfigurationField{configuration.Fields[1], configuration.Fields[0]}}
	other, err := reordered.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	if digest != other {
		t.Fatal("normalized configuration digest depends on field order")
	}
	candidate := AuditExportTargetDraft{Name: "siem", Description: "governed endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	binding := CredentialBinding{ID: "binding-1", ScopeID: "scope-1", SubjectID: "target-1", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	policy := ExportPolicy{MaxRows: 2, MaxBytes: 32, CredentialBindings: auditBindingResolver{binding: binding}}
	if err := candidate.ValidateWithPolicy(policy, "target-1", "scope-1"); err != nil {
		t.Fatal(err)
	}
	if err := candidate.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: 32}, "target-1", "scope-1"); err == nil {
		t.Fatal("expected missing authority to fail")
	}
	inactive := binding
	inactive.State = "revoked"
	if err := candidate.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: 32, CredentialBindings: auditBindingResolver{binding: inactive}}, "target-1", "scope-1"); err == nil {
		t.Fatal("expected inactive credential to fail")
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "governed endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: "binding-1", Revision: "1"}
	if err := target.ValidateWithPolicy(policy); err != nil {
		t.Fatal(err)
	}
	target.ConfigurationDigest = "sha256:wrong"
	if err := target.Validate(); err == nil {
		t.Fatal("expected mismatched configuration digest to fail")
	}
	probe := AuditExportProbeRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate, Payload: AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "payload", Reason: RedactionSecret}}}, Timeout: time.Second}
	if err := probe.ValidateWithPolicy(policy, "target-1"); err != nil {
		t.Fatal(err)
	}
}

func TestAuditDetailStreamAndAcknowledgementAreBounded(t *testing.T) {
	event := exportEvent(validAuditEvent("audit-1", time.Date(2026, time.July, 31, 0, 0, 0, 0, time.UTC)))
	batch := AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: event.Cursor, EndCursor: event.Cursor, Events: []AuditExportEvent{event}, CursorInterval: []string{event.Cursor}}
	if err := batch.Validate(); err != nil {
		t.Fatal(err)
	}
	ack := AuditExportAcknowledgement{TargetID: batch.TargetID, DeliveryID: batch.DeliveryID, StartCursor: batch.StartCursor, HighestContiguousCursor: batch.EndCursor}
	if err := ack.ValidateFor(batch); err != nil {
		t.Fatal(err)
	}
	ack.HighestContiguousCursor = "other"
	if err := ack.ValidateFor(batch); err == nil {
		t.Fatal("expected acknowledgement outside interval to fail")
	}
	payload := exportPayload(ExportFormatJSONL, []AuditExportEvent{event})
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: payload, Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: len(payload)}); err != nil {
		t.Fatal(err)
	}
	limited := AuditExportStream{Format: ExportFormatJSONL, LimitExceeded: &ExportLimitExceeded{Limit: ExportLimitRows, Maximum: 2}}
	if err := limited.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: 3}); err != nil {
		t.Fatalf("typed no-chunk limit result rejected: %v", err)
	}
	mixed := limited
	mixed.Chunks = stream.Chunks
	if err := mixed.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: 3}); err == nil {
		t.Fatal("typed limit result must not be mixed with successful chunks")
	}
	stream.Chunks[0].Events[0].Cursor = ""
	if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 2, MaxBytes: 3}); err == nil {
		t.Fatal("malformed stream event must fail")
	}
	base := validAuditEvent("audit-2", time.Now().UTC())
	detail := AuditEventDetailView{Event: exportSummary(base), RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: base.RequestContextID, CausationID: base.CausationID, CorrelationID: base.CorrelationID}, EvaluatedControls: base.EvaluatedControls, CredentialUses: []CredentialUseEvidence{{BindingID: "binding-1", Fingerprint: "sha256:credential-material-fingerprint", Purpose: CredentialPurposeAuditExport, Target: "target-1", Outcome: "succeeded", Availability: AvailabilityAvailable}}, RelatedGovernanceRecords: []RelatedGovernanceRecord{{Kind: "request", ID: "request-1", Availability: AvailabilityRedacted, Reason: AvailabilityReasonRetention, Redactions: []Redaction{{Field: "request", Reason: RedactionRetention}}}}, RelatedSubjects: []RelatedSubject{{Subject: base.Subject, Availability: AvailabilityAvailable}}, Redactions: []Redaction{{Field: "detail", Reason: RedactionNotAuthorized}}, authoritativeEvent: &base, authorityDigest: auditEventAuthorityDigest(base)}
	if err := detail.Validate(); err != nil {
		t.Fatal(err)
	}
	detail.EvaluatedControls[0].Authorization = nil
	if err := detail.Validate(); err == nil {
		t.Fatal("expected incomplete control provenance to fail")
	}
}

func TestAuditControlAndContractBoundaryMatrix(t *testing.T) {
	controls := []EvaluatedControl{{Kind: ControlAdmission, ControlID: "admission", Revision: "1", Digest: "sha256:a", Result: "allowed", Admission: &AdmissionControlProvenance{AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit, EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision"}}, {Kind: ControlProvider, ControlID: "provider", Revision: "1", Digest: "sha256:p", Result: "allowed", Provider: &ProviderControlProvenance{ProviderID: "provider-1", Release: "provider@1.2.3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}}
	for _, control := range controls {
		if err := control.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	controls[0].Provider = controls[1].Provider
	if err := controls[0].Validate(); err == nil {
		t.Fatal("expected mixed control provenance to fail")
	}
	now := time.Date(2026, time.July, 31, 0, 0, 0, 0, time.UTC)
	valid := validAuditEvent("audit-1", now)
	for _, mutate := range []func(*AuditEvent){func(event *AuditEvent) { event.PrincipalID = "" }, func(event *AuditEvent) { event.Category = "unknown" }, func(event *AuditEvent) { event.Subject = SubjectKey{} }, func(event *AuditEvent) { event.EvaluatedControls = nil }, func(event *AuditEvent) { event.Redactions = []Redaction{{Field: "x", Reason: "unknown"}} }} {
		event := valid
		mutate(&event)
		if err := event.Validate(); err == nil {
			t.Fatal("expected malformed audit event to fail")
		}
	}
	if err := (EvaluatedControl{Kind: "unknown", ControlID: "id", Revision: "1", Digest: "sha256:x", Result: "allowed"}).Validate(); err == nil {
		t.Fatal("expected unknown control kind to fail")
	}
	summary := exportSummary(valid)
	summary.Redactions = []Redaction{{Field: "x", Reason: "unknown"}}
	if err := summary.Validate(); err == nil {
		t.Fatal("expected malformed summary to fail")
	}
	if err := (AuditEventDetailView{Event: exportSummary(valid), RelatedSubjects: []RelatedSubject{{Subject: SubjectKey{}, Availability: AvailabilityAvailable}}}).Validate(); err == nil {
		t.Fatal("expected malformed related subject to fail")
	}
	if err := (AuditEventDetailView{Event: exportSummary(valid), CredentialUses: []CredentialUseEvidence{{BindingID: "binding", Fingerprint: "sha256:fp", Purpose: CredentialPurpose("other"), Target: "target", Outcome: "succeeded", Availability: AvailabilityAvailable}}}).Validate(); err == nil {
		t.Fatal("expected unauthorized credential evidence to fail")
	}
	query := QueryAuditEventsRequest{RequestContext: testRequestContext("scope", "request"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: now, End: now.Add(time.Hour)}}, PageSize: 1}
	if err := (ExportAuditEventsRequest{RequestContext: query.RequestContext, Filter: query.Filter, Format: ExportFormatCSV}).ValidateWithPolicy(DefaultQueryPolicy(), ExportPolicy{}); err == nil {
		t.Fatal("expected invalid export policy to fail")
	}
	if err := (ExportAuditEventsRequest{RequestContext: query.RequestContext, Filter: query.Filter, Format: "xml"}).ValidateWithPolicy(DefaultQueryPolicy(), DefaultExportPolicy()); err == nil {
		t.Fatal("expected invalid export format to fail")
	}
	for _, configuration := range []AuditExportConfiguration{{SchemaVersion: ExportSchemaWebhookV1}, {SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldTenant, Value: "tenant"}}}, {SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://wrong.example.test"}}}} {
		if err := configuration.Validate(); err == nil {
			t.Fatal("expected invalid export configuration to fail")
		}
	}
	if spec, ok := exportSchemaForType(ExportTypeSyslog); !ok || !validExportEndpoint(spec, "tls://siem.example.test:6514") || validExportEndpoint(spec, "tls://user@siem.example.test:6514") {
		t.Fatal("expected positive syslog schema and endpoint rules")
	}
	if _, ok := exportSchemaForType("unknown"); ok {
		t.Fatal("unknown export type unexpectedly accepted")
	}
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	candidate := AuditExportTargetDraft{Name: "target", Description: "desc", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding"}
	binding := CredentialBinding{ID: "binding", ScopeID: "scope", SubjectID: "target", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	if err := binding.ValidateFor("other", ExportTypeWebhook, "target"); err == nil {
		t.Fatal("expected scope mismatch to fail")
	}
	targetDigest, err := configuration.DigestFor(ExportTypeWebhook, "binding")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target", ScopeID: "scope", Name: "target", Description: "desc", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: targetDigest, CredentialBindingID: "binding", Revision: "1"}
	target.CredentialBindingID = ""
	if err := target.Validate(); err == nil {
		t.Fatal("expected webhook target without credential binding to fail")
	}
	target.CredentialBindingID = "binding"
	if err := target.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{}}); err == nil {
		t.Fatal("expected target resolver failure")
	}
	if err := validateAuthoritativeCredentialBinding(ExportPolicy{CredentialBindings: auditBindingResolver{}}, "binding", "scope", ExportTypeWebhook, "target"); err == nil {
		t.Fatal("expected unresolved binding to fail")
	}
	if containsBindingUsage(nil, CredentialBindingUsageAuditExport) || containsExportType(nil, ExportTypeWebhook) {
		t.Fatal("empty binding catalogs cannot authorize")
	}
	if err := validateRedactions([]Redaction{{Field: "", Reason: RedactionSecret}}); err == nil {
		t.Fatal("expected malformed redaction to fail")
	}
	event := exportEvent(valid)
	for _, stream := range []AuditExportStream{{Format: "xml"}, {Format: ExportFormatCSV, Chunks: []AuditExportChunk{{Payload: nil, Events: []AuditExportEvent{event}}}}, {Format: ExportFormatCSV, Chunks: []AuditExportChunk{{Payload: []byte("row"), Events: []AuditExportEvent{event, event}}}}} {
		if err := stream.ValidateWithPolicy(ExportPolicy{MaxRows: 1, MaxBytes: 3}); err == nil {
			t.Fatal("expected bounded stream violation to fail")
		}
	}
	probe := AuditExportProbeRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: candidate, Payload: AuditExportProbePayload{Summary: SafeSummary{Code: "export-probe"}, Redactions: []Redaction{{Field: "x", Reason: RedactionSecret}}}, Timeout: time.Second}
	if err := probe.Validate(); err != nil {
		t.Fatal(err)
	}
	probe.Timeout = 0
	if err := probe.ValidateWithPolicy(ExportPolicy{CredentialBindings: auditBindingResolver{binding: binding}}, "target-1"); err == nil {
		t.Fatal("expected zero timeout to fail")
	}
	for _, result := range []AuditExportProbeResult{{Outcome: "other"}, {Outcome: AuditExportProbeSucceeded, FailureCode: ProbeFailureTimeout}, {Outcome: AuditExportProbeFailed, FailureCode: ProbeFailureTimeout, Redactions: []Redaction{{Field: "", Reason: RedactionSecret}}}} {
		if err := result.Validate(); err == nil {
			t.Fatal("expected invalid probe result to fail")
		}
	}
	badEvent := event
	badEvent.Cursor = ""
	if err := badEvent.Validate(); err == nil {
		t.Fatal("expected cursor-less exported event to fail")
	}
	badEvent = event
	badEvent.Redactions = []Redaction{{Field: "", Reason: RedactionSecret}}
	if err := badEvent.Validate(); err == nil {
		t.Fatal("expected invalid export redaction to fail")
	}
	badBatch := AuditExportBatch{TargetID: "target", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:x", DeliveryID: "delivery", StartCursor: event.Cursor, EndCursor: event.Cursor, Events: []AuditExportEvent{event}, CursorInterval: []string{"other"}}
	if err := badBatch.Validate(); err == nil {
		t.Fatal("expected unbound batch cursor to fail")
	}
}

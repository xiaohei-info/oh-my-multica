package audit

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestAuditPublicWireSerializersFailClosed(t *testing.T) {
	event := validAuditEvent("audit-wire-serializers", time.Now().UTC())
	summary := exportSummary(event)
	if _, err := json.Marshal(summary); err != nil {
		t.Fatalf("valid audit summary did not serialize: %v", err)
	}
	invalidSummary := summary
	invalidSummary.ChangeSummary = &SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "unknown", Value: SafeSummaryValueCompleted}}}
	if _, err := json.Marshal(invalidSummary); err == nil {
		t.Fatal("invalid audit summary crossed the JSON boundary")
	}
	invalidSafeSummary := SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "unknown", Value: SafeSummaryValueCompleted}}}
	if _, err := json.Marshal(invalidSafeSummary); err == nil {
		t.Fatal("invalid safe summary crossed the JSON boundary")
	}
	binding, err := issueTestAuditEventStoreBinding(event)
	if err != nil {
		t.Fatal(err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatal(err)
	}
	detail.RequestContextSummary = RequestContextSummary{PrincipalID: event.PrincipalID, ScopeID: event.ScopeID, Source: "api", RequestContextID: event.RequestContextID, RequestID: "request-http-1", CausationID: event.CausationID, CorrelationID: event.CorrelationID}
	detailWire, err := json.Marshal(detail)
	if err != nil {
		t.Fatalf("valid audit detail did not serialize: %v", err)
	}
	if !strings.Contains(string(detailWire), `"request_context_id":"request-1"`) || !strings.Contains(string(detailWire), `"request_id":"request-http-1"`) {
		t.Fatalf("audit detail wire collapsed request identities: %s", detailWire)
	}
	detail.Event.ChangeSummary = invalidSafeSummaryPointer()
	if _, err := json.Marshal(detail); err == nil {
		t.Fatal("invalid audit detail crossed the JSON boundary")
	}
	probe := AuditExportProbePayload{Summary: SafeSummary{Code: SafeSummaryCodeExportProbe}, Redactions: []Redaction{{Field: "endpoint", Reason: RedactionSecret}}}
	if _, err := json.Marshal(probe); err != nil {
		t.Fatalf("valid probe payload did not serialize: %v", err)
	}
	probe.Summary.Code = SafeSummaryCode("unknown")
	if _, err := json.Marshal(probe); err == nil {
		t.Fatal("invalid probe payload crossed the JSON boundary")
	}
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	configurationDigest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "events", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: configurationDigest, CredentialBindingID: "binding-1", Revision: "1"}
	if _, err := json.Marshal(target); err != nil {
		t.Fatalf("valid export target did not serialize: %v", err)
	}
	invalidTarget := target
	invalidTarget.Configuration.Fields = []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://user:password@example.test/events"}}
	if _, err := json.Marshal(invalidTarget); err == nil {
		t.Fatal("invalid export target crossed the JSON boundary")
	}
	if _, err := json.Marshal(SaveAuditExportTargetResult{Target: target}); err != nil {
		t.Fatalf("valid target result did not serialize: %v", err)
	}
	probeResult := AuditExportProbeResult{Outcome: AuditExportProbeSucceeded}
	if _, err := json.Marshal(probeResult); err != nil {
		t.Fatalf("valid probe result did not serialize: %v", err)
	}
	probeResult.Outcome = AuditExportProbeOutcome("unknown")
	if _, err := json.Marshal(probeResult); err == nil {
		t.Fatal("invalid probe result crossed the JSON boundary")
	}
	exported := exportEvent(event)
	if _, err := json.Marshal(exported); err != nil {
		t.Fatalf("valid export event did not serialize: %v", err)
	}
	exported.ID = ""
	if _, err := json.Marshal(exported); err == nil {
		t.Fatal("invalid export event crossed the JSON boundary")
	}
	streamEvent := exportEvent(event)
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{streamEvent}), Events: []AuditExportEvent{streamEvent}}}}
	if _, err := json.Marshal(stream); err != nil {
		t.Fatalf("valid export stream did not serialize: %v", err)
	}
	stream.Chunks[0].Payload = []byte("not-canonical")
	if _, err := json.Marshal(stream); err == nil {
		t.Fatal("invalid export stream crossed the JSON boundary")
	}
	batch := AuditExportBatch{TargetID: "target-1", TargetScopeID: streamEvent.ScopeID, TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: streamEvent.Cursor, EndCursor: streamEvent.Cursor, Events: []AuditExportEvent{streamEvent}, CursorInterval: []string{streamEvent.Cursor}}
	if _, err := json.Marshal(batch); err != nil {
		t.Fatalf("valid export batch did not serialize: %v", err)
	}
	ack := AuditExportAcknowledgement{TargetID: batch.TargetID, DeliveryID: batch.DeliveryID, StartCursor: batch.StartCursor, HighestContiguousCursor: batch.EndCursor}
	if _, err := json.Marshal(ack); err != nil {
		t.Fatalf("valid export acknowledgement did not serialize: %v", err)
	}
	if _, err := json.Marshal(AuditExportAcknowledgement{}); err == nil {
		t.Fatal("invalid export acknowledgement crossed the JSON boundary")
	}
}

func invalidSafeSummaryPointer() *SafeSummary {
	return &SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "unknown", Value: SafeSummaryValueCompleted}}}
}

func TestAuditChangeSummaryRejectsSensitiveAndUnboundedWireContent(t *testing.T) {
	event := validAuditEvent("audit-change-summary", time.Now().UTC())
	for _, summary := range []string{"Bearer secret-token", "password=hidden", strings.Repeat("safe ", MaxAuditChangeSummaryBytes)} {
		candidate := event
		candidate.ChangeSummary = &SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "reason", Value: SafeSummaryValue(summary)}}}
		if err := candidate.Validate(); err == nil {
			t.Fatalf("Validate() accepted unsafe change summary %q", summary[:min(len(summary), 24)])
		}
		if _, err := json.Marshal(candidate); err == nil {
			t.Fatalf("MarshalJSON() accepted unsafe change summary %q", summary[:min(len(summary), 24)])
		}
	}
	candidate := event
	candidate.ChangeSummary = &SafeSummary{Code: SafeSummaryCodeStateWrite, Fields: []SafeSummaryField{{Name: "reason", Value: SafeSummaryValueDeploymentCompleted}}}
	if err := candidate.Validate(); err != nil {
		t.Fatalf("safe change summary rejected: %v", err)
	}
	if _, err := json.Marshal(candidate); err != nil {
		t.Fatalf("safe change summary failed canonical JSON: %v", err)
	}
}

func TestAuditWireRedactionMatrixRejectsUndeclaredFields(t *testing.T) {
	event := validAuditEvent("audit-wire-matrix", time.Now().UTC())
	event.ChangeSummary = &SafeSummary{Code: "state-write", Fields: []SafeSummaryField{{Name: "reason", Value: "deployment completed"}}}
	if err := event.Validate(); err != nil {
		t.Fatal(err)
	}
	binding, err := issueTestAuditEventStoreBinding(event)
	if err != nil {
		t.Fatal(err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatal(err)
	}
	detail.RequestContextSummary = RequestContextSummary{PrincipalID: event.PrincipalID, ScopeID: event.ScopeID, Source: "api", RequestContextID: event.RequestContextID, RequestID: event.RequestContextID, CausationID: event.CausationID, CorrelationID: event.CorrelationID}
	wire, err := json.Marshal(detail)
	if err != nil {
		t.Fatal(err)
	}
	text := string(wire)
	if !strings.Contains(text, "change_summary") || strings.Contains(text, "authoritativeEvent") || strings.Contains(text, "authorityDigest") {
		t.Fatalf("detail wire violated redaction matrix: %s", text)
	}
	bad := event
	bad.ChangeSummary = &SafeSummary{Code: "state-write", Fields: []SafeSummaryField{{Name: "api_key", Value: "placeholder"}}}
	if err := bad.Validate(); err == nil {
		t.Fatal("undeclared safe-summary field was accepted")
	}
	if _, err := json.Marshal(bad); err == nil {
		t.Fatal("undeclared safe-summary field crossed the canonical wire")
	}
	if _, err := json.Marshal(AuditExportProbeResult{Outcome: AuditExportProbeOutcome("unknown")}); err == nil {
		t.Fatal("invalid probe result crossed the canonical wire")
	}
	if _, err := json.Marshal(AuditExportTarget{}); err == nil {
		t.Fatal("invalid export target crossed the canonical wire")
	}
}

func TestAuditAuthorityDigestMatchesCanonicalWireRoundTrip(t *testing.T) {
	location := time.FixedZone("offset", 2*60*60)
	offsetEvent := validAuditEvent("audit-offset", time.Date(2026, time.August, 1, 14, 0, 0, 0, location))
	offsetWire, err := json.Marshal(offsetEvent)
	if err != nil || !strings.Contains(string(offsetWire), `"occurred_at":"2026-08-01T12:00:00Z"`) {
		t.Fatalf("audit event timestamp was not normalized to UTC: %s (err=%v)", offsetWire, err)
	}
	digest := "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	for _, release := range []string{"package@1.2.3--alpha@" + digest, "package@1.2.3-alpha-@" + digest, "package@1.a.3@" + digest, "package@1.2.patch@" + digest} {
		if isExactPackageRelease(release) {
			t.Fatalf("invalid prerelease boundary accepted: %q", release)
		}
	}
	event := validAuditEvent("audit-wire-roundtrip", time.Now().UTC())
	event.ChangeSummary = &SafeSummary{Code: "state-write", Fields: []SafeSummaryField{{Name: "status", Value: "completed"}}}
	wire, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	var decoded AuditEvent
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	if got, want := auditEventAuthorityDigest(decoded), auditEventAuthorityDigest(event); got != want {
		t.Fatalf("canonical authority digest changed across JSON round trip: got %s want %s", got, want)
	}
	binding, err := issueTestAuditEventStoreBinding(event)
	if err != nil {
		t.Fatal(err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatal(err)
	}
	detail.RequestContextSummary = RequestContextSummary{PrincipalID: event.PrincipalID, ScopeID: event.ScopeID, Source: "api", RequestContextID: event.RequestContextID, RequestID: event.RequestContextID, CausationID: event.CausationID, CorrelationID: event.CorrelationID}
	detailWire, err := json.Marshal(detail)
	if err != nil {
		t.Fatal(err)
	}
	var decodedDetail AuditEventDetailView
	if err := json.Unmarshal(detailWire, &decodedDetail); err != nil {
		t.Fatal(err)
	}
	if err := decodedDetail.ValidateWithStore(binding); err != nil {
		t.Fatalf("detail failed canonical JSON rebind: %v", err)
	}
}

func TestAuditSaveUpdateCredentialBindsLoadedTarget(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	otherTargetBinding := CredentialBinding{ID: "binding-2", ScopeID: "scope-1", SubjectID: "target-2", State: CredentialBindingActive, Usages: []CredentialBindingUsage{CredentialBindingUsageAuditExport}, ExportTypes: []ExportType{ExportTypeWebhook}}
	policy := ExportPolicy{CredentialBindings: auditBindingResolver{binding: otherTargetBinding}}
	digest, err := configuration.DigestFor(ExportTypeWebhook, otherTargetBinding.ID)
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "webhook", Description: "endpoint", Revision: "7", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: otherTargetBinding.ID}
	request := SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), ExpectedRevision: target.Revision, Draft: AuditExportTargetDraft{Name: target.Name, Description: target.Description, ExportType: target.ExportType, Configuration: configuration, CredentialBindingID: target.CredentialBindingID}}
	if err := request.ValidateForTargetWithPolicy(policy, target); err == nil {
		t.Fatal("update accepted a credential binding whose subject belongs to another target")
	}
}

func TestAuditExportTargetCASRequiresCommittedRevision(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	digest, err := configuration.DigestFor(ExportTypeSyslog, "")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "target", Description: "description", Revision: "current", ExportType: ExportTypeSyslog, Configuration: configuration, ConfigurationDigest: digest}
	save := SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), ExpectedRevision: "stale", Draft: AuditExportTargetDraft{Name: "target", Description: "description", ExportType: ExportTypeSyslog, Configuration: configuration}}
	if err := save.ValidateForTarget(target); err == nil {
		t.Fatal("stale save revision was accepted")
	}
	if err := save.ValidateForTarget(AuditExportTarget{ScopeID: target.ScopeID, Revision: target.Revision}); err == nil {
		t.Fatal("incomplete persisted target was accepted")
	}
	save.ExpectedRevision = target.Revision
	if err := save.ValidateForTarget(target); err != nil {
		t.Fatalf("current save revision rejected: %v", err)
	}
	protected := save
	protected.Draft.Configuration.Fields[0].Value = "tls://other.example.test:6514"
	if err := protected.ValidateForTarget(target); err == nil {
		t.Fatal("same-revision protected save change was accepted")
	} else if _, ok := err.(TargetReplacementRequiredError); !ok {
		t.Fatalf("protected save change returned wrong error type: %T", err)
	}
	target.Configuration = configuration
	target.Configuration.Fields = []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}
	target.ConfigurationDigest = digest
	update := UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "stale", Name: "target", Description: "description"}
	if err := update.ValidateForTarget(target); err == nil {
		t.Fatal("stale update revision was accepted")
	}
	update.ExpectedRevision = target.Revision
	if err := update.ValidateForTarget(target); err != nil {
		t.Fatalf("current update revision rejected: %v", err)
	}
	enable := EnableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "stale", ConfigurationDigest: "stale"}
	if err := enable.ValidateForTarget(target); err == nil {
		t.Fatal("stale enable revision was accepted")
	}
	enable.ExpectedRevision = target.Revision
	if err := enable.ValidateForTarget(target); err == nil {
		t.Fatal("stale enable configuration digest was accepted")
	}
	disable := DisableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "stale", ConfigurationDigest: "stale"}
	if err := disable.ValidateForTarget(target); err == nil {
		t.Fatal("stale disable revision was accepted")
	}
	disable.ExpectedRevision = target.Revision
	if err := disable.ValidateForTarget(target); err == nil {
		t.Fatal("stale disable configuration digest was accepted")
	}
	replacement := AuditExportTargetDraft{Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}}
	replace := ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), CurrentTargetID: target.ID, ExpectedRevision: "stale", Replacement: replacement}
	if err := replace.ValidateWithPolicy(DefaultExportPolicy(), "target-2", target); err == nil {
		t.Fatal("stale replacement revision was accepted")
	}
	replace.ExpectedRevision = target.Revision
	if err := replace.ValidateWithPolicy(DefaultExportPolicy(), "target-2", target); err != nil {
		t.Fatalf("current replacement revision rejected: %v", err)
	}
}

func TestAuditExportDraftHasNoCallerIdentityOrDerivedDigest(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	draft := AuditExportTargetDraft{Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: configuration}
	if err := draft.Validate(); err != nil {
		t.Fatalf("draft Validate() error = %v", err)
	}
	payload, err := json.Marshal(SaveAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if strings.Contains(text, "audit_export_target_id") || strings.Contains(text, "configuration_digest") {
		t.Fatalf("public create request exposed server-owned fields: %s", text)
	}
	if _, ok := any(draft).(struct{ TargetID string }); ok {
		t.Fatal("draft unexpectedly carries a target identity")
	}
}

func TestAuditExportReplacementRejectsCrossScopeBeforeCredentialLookup(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	replacement := AuditExportTargetDraft{Name: "webhook", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1"}
	request := ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-2", "request-1"), CurrentTargetID: "target-1", ExpectedRevision: "1", Replacement: replacement}
	if err := request.Validate(); err != nil {
		t.Fatalf("draft replacement shape should validate before authoritative scope binding: %v", err)
	}
	resolver := &countingBindingResolver{}
	currentTarget := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, CredentialBindingID: "binding-1", Revision: "1"}
	currentTarget.ConfigurationDigest, _ = configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err := request.ValidateWithPolicy(ExportPolicy{CredentialBindings: resolver}, "target-2", currentTarget); err == nil {
		t.Fatal("cross-scope replacement must be rejected before policy lookup")
	}
	if resolver.calls != 0 {
		t.Fatalf("cross-scope replacement performed credential lookup %d times", resolver.calls)
	}
	if err := (ReplaceAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), CurrentTargetID: "target-1", ExpectedRevision: "1", Replacement: replacement}).ValidateWithPolicy(ExportPolicy{CredentialBindings: resolver}, "target-1", currentTarget); err == nil {
		t.Fatal("replacement that reuses the current target identity must fail")
	}
	if resolver.calls != 0 {
		t.Fatalf("same-identity replacement performed credential lookup %d times", resolver.calls)
	}
}

func TestAuditLifecycleBindsTrustedContextToPersistedTarget(t *testing.T) {
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Revision: "1", ConfigurationDigest: "sha256:config"}
	update := UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "1", Name: "target", Description: "description"}
	if err := update.ValidateForTarget(target); err != nil {
		t.Fatalf("update target context rejected: %v", err)
	}
	if err := (UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-2", "request-1"), TargetID: target.ID, ExpectedRevision: "1", Name: "target", Description: "description"}).ValidateForTarget(target); err == nil {
		t.Fatal("update with mismatched target scope must fail")
	}
	enable := EnableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "1", ConfigurationDigest: target.ConfigurationDigest}
	if err := enable.ValidateForTarget(target); err != nil {
		t.Fatalf("enable target context rejected: %v", err)
	}
	if err := (EnableAuditExportTargetRequest{RequestContext: testRequestContext("scope-2", "request-1"), TargetID: target.ID, ExpectedRevision: "1", ConfigurationDigest: target.ConfigurationDigest}).ValidateForTarget(target); err == nil {
		t.Fatal("enable with mismatched target scope must fail")
	}
	disable := DisableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: target.ID, ExpectedRevision: "1", ConfigurationDigest: target.ConfigurationDigest}
	if err := disable.ValidateForTarget(target); err != nil {
		t.Fatalf("disable target context rejected: %v", err)
	}
	if err := (DisableAuditExportTargetRequest{RequestContext: testRequestContext("scope-1", "request-1"), TargetID: "other", ExpectedRevision: "1", ConfigurationDigest: target.ConfigurationDigest}).ValidateForTarget(target); err == nil {
		t.Fatal("mutation of a different target must fail")
	}
}

func TestAuditExportBatchBindsPersistedTargetScopeAndDigest(t *testing.T) {
	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}
	digest, err := configuration.DigestFor(ExportTypeSyslog, "")
	if err != nil {
		t.Fatal(err)
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "syslog", Description: "endpoint", ExportType: ExportTypeSyslog, Configuration: configuration, ConfigurationDigest: digest, Enabled: true, Revision: "1"}
	event := exportEvent(validAuditEvent("audit-batch", time.Now().UTC()))
	batch := AuditExportBatch{TargetID: target.ID, TargetScopeID: target.ScopeID, TargetConfigurationDigest: digest, DeliveryID: "delivery-1", StartCursor: event.Cursor, EndCursor: event.Cursor, Events: []AuditExportEvent{event}, CursorInterval: []string{event.Cursor}}
	if err := batch.ValidateForTarget(target); err != nil {
		t.Fatalf("batch target binding rejected: %v", err)
	}
	mixed := batch
	mixed.Events = []AuditExportEvent{event, event}
	mixed.CursorInterval = []string{event.Cursor, event.Cursor}
	mixed.Events[1].ID = "audit-other"
	mixed.Events[1].ScopeID = "scope-2"
	if err := mixed.Validate(); err == nil {
		t.Fatal("batch with an event from another scope must fail")
	}
	wrongDigest := batch
	wrongDigest.TargetConfigurationDigest = "sha256:wrong"
	if err := wrongDigest.ValidateForTarget(target); err == nil {
		t.Fatal("batch with a stale target digest must fail")
	}
	disabled := target
	disabled.Enabled = false
	if err := batch.ValidateForTarget(disabled); err == nil {
		t.Fatal("disabled target must not deliver a matching batch")
	}
}

func TestAuditExportStreamBindsTrustedRequestScope(t *testing.T) {
	event := exportEvent(validAuditEvent("audit-stream", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	request := ExportAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: event.OccurredAt.Add(-time.Hour), End: event.OccurredAt.Add(time.Hour)}}, Format: ExportFormatJSONL}
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{event}), Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateForRequest(request, DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("stream trusted-scope binding rejected: %v", err)
	}
	event.ScopeID = "scope-2"
	stream.Chunks[0].Events[0] = event
	if err := stream.ValidateForRequest(request, DefaultQueryPolicy(), DefaultExportPolicy()); err == nil {
		t.Fatal("stream containing another scope must fail")
	}
}

func TestAuditExportStreamBindsEveryFilterDimension(t *testing.T) {
	base := validAuditEvent("audit-filter", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC))
	base.ProjectID = "project-1"
	event := exportEvent(base)
	filter := AuditEventFilter{TimeRange: TimeRange{Start: base.OccurredAt.Add(-time.Hour), End: base.OccurredAt.Add(time.Hour)}, ProjectID: "project-1", Category: base.Category, Action: base.Action, Outcome: base.Outcome, PrincipalID: base.PrincipalID, Subject: &SubjectKey{Kind: base.Subject.Kind, ID: base.Subject.ID}, CorrelationID: base.CorrelationID}
	request := ExportAuditEventsRequest{RequestContext: testRequestContext(base.ScopeID, "request-1"), Filter: filter, Format: ExportFormatJSONL}
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{event}), Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateForRequest(request, DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("matching export filter rejected: %v", err)
	}
	mutations := []func(*AuditEventFilter){
		func(f *AuditEventFilter) { f.ProjectID = "other" },
		func(f *AuditEventFilter) { f.Category = CategoryAuthentication },
		func(f *AuditEventFilter) { f.Action = "other" },
		func(f *AuditEventFilter) { f.Outcome = OutcomeFailed },
		func(f *AuditEventFilter) { f.PrincipalID = "other" },
		func(f *AuditEventFilter) { f.Subject = &SubjectKey{Kind: base.Subject.Kind, ID: "other"} },
		func(f *AuditEventFilter) { f.CorrelationID = "other" },
	}
	for _, mutate := range mutations {
		invalid := request
		invalid.Filter = filter
		mutate(&invalid.Filter)
		if err := stream.ValidateForRequest(invalid, DefaultQueryPolicy(), DefaultExportPolicy()); err == nil {
			t.Fatal("export stream outside a requested filter dimension must fail")
		}
	}
}

func TestCredentialUseEvidenceAvailabilityMatrix(t *testing.T) {
	available := CredentialUseEvidence{BindingID: "binding-1", Fingerprint: "sha256:fingerprint", Purpose: CredentialPurposeAuditExport, Target: "target-1", Outcome: "succeeded", Availability: AvailabilityAvailable}
	if err := available.Validate(); err != nil {
		t.Fatalf("available credential evidence rejected: %v", err)
	}
	if _, err := json.Marshal(available); err != nil {
		t.Fatalf("valid credential evidence did not serialize: %v", err)
	}
	unsafeDirect := available
	unsafeDirect.Target = "https://user:password@example.test"
	if _, err := json.Marshal(unsafeDirect); err == nil {
		t.Fatal("unsafe credential evidence crossed its JSON boundary")
	}
	for _, mutate := range []func(*CredentialUseEvidence){
		func(value *CredentialUseEvidence) { value.Fingerprint = "Bearer secret-token" },
		func(value *CredentialUseEvidence) { value.Target = "https://user:password@example.test" },
		func(value *CredentialUseEvidence) { value.Outcome = "header-value" },
	} {
		candidate := available
		mutate(&candidate)
		if err := candidate.Validate(); err == nil {
			t.Fatal("unsafe credential evidence value was accepted")
		}
	}
	base := validAuditEvent("credential-wire", time.Now().UTC())
	binding, err := issueTestAuditEventStoreBinding(base)
	if err != nil {
		t.Fatal(err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatal(err)
	}
	detail.RequestContextSummary = RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: "request-http-1", CausationID: base.CausationID, CorrelationID: base.CorrelationID}
	unsafe := available
	unsafe.Fingerprint = "Bearer secret-token"
	detail.CredentialUses = []CredentialUseEvidence{unsafe}
	if _, err := json.Marshal(detail); err == nil {
		t.Fatal("unsafe credential evidence crossed the detail JSON boundary")
	}
	fields := []string{"binding_id", "fingerprint", "target", "outcome"}
	for _, test := range []struct {
		name         string
		availability RelatedRecordAvailability
		reason       RedactionReason
	}{
		{name: "redacted", availability: AvailabilityRedacted, reason: RedactionRetention},
		{name: "not-authorized", availability: AvailabilityNotAuthorized, reason: RedactionNotAuthorized},
		{name: "unavailable", availability: AvailabilityUnavailable, reason: RedactionRetention},
	} {
		evidence := CredentialUseEvidence{Purpose: CredentialPurposeAuditExport, Availability: test.availability}
		for _, field := range fields {
			evidence.Redactions = append(evidence.Redactions, Redaction{Field: field, Reason: test.reason})
		}
		if err := evidence.Validate(); err != nil {
			t.Fatalf("%s credential evidence rejected: %v", test.name, err)
		}
		partial := evidence
		partial.Redactions = append([]Redaction(nil), evidence.Redactions[:len(evidence.Redactions)-1]...)
		if err := partial.Validate(); err == nil {
			t.Fatalf("%s credential evidence with partial field redactions must fail", test.name)
		}
	}
	preserved := CredentialUseEvidence{BindingID: "binding-1", Purpose: CredentialPurposeAuditExport, Availability: AvailabilityRedacted, Redactions: []Redaction{{Field: "fingerprint", Reason: RedactionRetention}, {Field: "target", Reason: RedactionRetention}, {Field: "outcome", Reason: RedactionRetention}}}
	if err := preserved.Validate(); err != nil {
		t.Fatalf("minimum non-sensitive credential identity rejected: %v", err)
	}
	populated := available
	populated.Redactions = []Redaction{{Field: "target", Reason: RedactionSecret}}
	if err := populated.Validate(); err == nil {
		t.Fatal("populated credential evidence must not be marked redacted")
	}
	unknown := CredentialUseEvidence{Purpose: CredentialPurposeAuditExport, Availability: AvailabilityRedacted, Redactions: []Redaction{{Field: "secret", Reason: RedactionSecret}}}
	if err := unknown.Validate(); err == nil {
		t.Fatal("credential evidence must reject unknown redaction fields")
	}
	redacted := CredentialUseEvidence{Purpose: CredentialPurposeAuditExport, Availability: AvailabilityRedacted, Redactions: []Redaction{{Field: "binding_id", Reason: RedactionRetention}, {Field: "fingerprint", Reason: RedactionRetention}, {Field: "target", Reason: RedactionRetention}, {Field: "outcome", Reason: RedactionRetention}}}
	redacted.Redactions[0].Reason = RedactionNotAuthorized
	if err := redacted.Validate(); err == nil {
		t.Fatal("redacted credential evidence must reject not-authorized reasons")
	}
	unavailable := redacted
	unavailable.Availability = AvailabilityUnavailable
	for index := range unavailable.Redactions {
		unavailable.Redactions[index].Reason = RedactionRetention
	}
	unavailable.Redactions[3].Field = "binding_id"
	if err := unavailable.Validate(); err == nil {
		t.Fatal("credential evidence must reject duplicate omission reasons")
	}
}

func TestAuditDetailBindingRequiresVerifiedStoreEvent(t *testing.T) {
	base := validAuditEvent("audit-authority", time.Now().UTC())
	if _, err := NewAuditEventDetailView(base); err == nil {
		t.Fatal("direct caller event must not become a detail authority")
	}
	binding, err := issueTestAuditEventStoreBinding(base)
	if err != nil {
		t.Fatalf("issueTestAuditEventStoreBinding() error = %v", err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		t.Fatalf("NewAuditEventDetailViewFromStore() error = %v", err)
	}
	detail.RequestContextSummary = RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: "request-http-1", CausationID: base.CausationID, CorrelationID: base.CorrelationID}
	if detail.RequestContextSummary.RequestID == detail.RequestContextSummary.RequestContextID {
		t.Fatal("detail fixture must keep request and persisted context identities distinct")
	}
	detail.EvaluatedControls = append([]EvaluatedControl(nil), base.EvaluatedControls...)
	if err := detail.Validate(); err != nil {
		t.Fatalf("constructor-bound detail Validate() error = %v", err)
	}
	unsafeDetail := detail
	unsafeDetail.CredentialUses = []CredentialUseEvidence{{BindingID: "binding-1", Fingerprint: "Bearer secret-token", Purpose: CredentialPurposeAuditExport, Target: "target-1", Outcome: "succeeded", Availability: AvailabilityAvailable}}
	if _, err := json.Marshal(unsafeDetail); err == nil {
		t.Fatal("unsafe credential evidence crossed the detail JSON boundary")
	}
	other := validAuditEvent("audit-other", time.Now().UTC())
	other.EvaluatedControls[0].ControlID = "other-policy"
	swapped := detail
	swapped.EvaluatedControls = other.EvaluatedControls
	swapped.Event.EvaluatedControlAnchors = exportSummary(other).EvaluatedControlAnchors
	if err := swapped.Validate(); err == nil {
		t.Fatal("detail must reject controls rebound while event identity is preserved")
	}
	mutated := detail
	mutated.EvaluatedControls = append([]EvaluatedControl(nil), detail.EvaluatedControls...)
	mutated.EvaluatedControls[0].Authorization = &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "foreign-grant"}
	if err := mutated.Validate(); err == nil {
		t.Fatal("detail must reject provenance mutation hidden behind an unchanged control anchor")
	}
	encoded, err := json.Marshal(detail)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "authoritativeEvent") || strings.Contains(string(encoded), "authoritative_event") {
		t.Fatal("authoritative event binding must remain outside the JSON DTO")
	}
	var decoded AuditEventDetailView
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if err := decoded.Validate(); err == nil {
		t.Fatal("JSON-decoded detail must require rebinding to the authoritative store event")
	}
	if err := decoded.ValidateWithStore(binding); err != nil {
		t.Fatalf("JSON-decoded detail failed authoritative rebinding: %v", err)
	}
}

func TestAuditDetailBindsPrincipalAndControlsToImmutableEvent(t *testing.T) {
	base := validAuditEvent("audit-anchor", time.Now().UTC())
	detail := AuditEventDetailView{Event: exportSummary(base), RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: base.RequestContextID, CausationID: base.CausationID, CorrelationID: base.CorrelationID}, EvaluatedControls: base.EvaluatedControls, authoritativeEvent: &base, authorityDigest: auditEventAuthorityDigest(base)}
	if err := detail.Validate(); err != nil {
		t.Fatalf("anchored detail rejected: %v", err)
	}
	principalMismatch := detail
	principalMismatch.RequestContextSummary.PrincipalID = "other-principal"
	if err := principalMismatch.Validate(); err == nil {
		t.Fatal("detail with mismatched principal must fail")
	}
	copiedControl := detail
	copiedControl.EvaluatedControls = append([]EvaluatedControl(nil), detail.EvaluatedControls...)
	copiedControl.EvaluatedControls[0].ControlID = "other-policy"
	if err := copiedControl.Validate(); err == nil {
		t.Fatal("detail with controls copied from another event must fail")
	}
	other := validAuditEvent("audit-other", time.Now().UTC())
	other.EvaluatedControls[0].ControlID = "other-policy"
	rebound := detail
	rebound.EvaluatedControls = other.EvaluatedControls
	rebound.Event.EvaluatedControlAnchors = exportSummary(other).EvaluatedControlAnchors
	if err := rebound.Validate(); err == nil {
		t.Fatal("detail must reject a caller-rebound control/anchor pair")
	}
	missingAnchor := detail
	missingAnchor.Event.EvaluatedControlAnchors = nil
	if err := missingAnchor.Validate(); err == nil {
		t.Fatal("detail without immutable control anchors must fail")
	}
}

func TestRelatedEvidenceRequiresStructuredAvailabilityReasons(t *testing.T) {
	base := validAuditEvent("audit-availability", time.Now().UTC())
	detail := AuditEventDetailView{Event: exportSummary(base), RequestContextSummary: RequestContextSummary{PrincipalID: base.PrincipalID, ScopeID: base.ScopeID, Source: "api", RequestContextID: base.RequestContextID, RequestID: base.RequestContextID, CausationID: base.CausationID, CorrelationID: base.CorrelationID}, EvaluatedControls: base.EvaluatedControls, authoritativeEvent: &base, authorityDigest: auditEventAuthorityDigest(base)}
	detail.RelatedGovernanceRecords = []RelatedGovernanceRecord{{Kind: "request", ID: "request-1", Availability: AvailabilityUnavailable, Reason: AvailabilityReasonRetention}}
	if err := detail.Validate(); err != nil {
		t.Fatalf("unavailable related record rejected: %v", err)
	}
	withoutReason := detail
	withoutReason.RelatedGovernanceRecords[0].Reason = ""
	if err := withoutReason.Validate(); err == nil {
		t.Fatal("unavailable related record without a structured reason must fail")
	}
	redacted := detail
	redacted.RelatedGovernanceRecords = []RelatedGovernanceRecord{{Kind: "request", ID: "request-1", Availability: AvailabilityRedacted, Reason: AvailabilityReasonRetention, Redactions: []Redaction{{Field: "request", Reason: RedactionRetention}}}}
	if err := redacted.Validate(); err != nil {
		t.Fatalf("redacted related record rejected: %v", err)
	}
	redacted.RelatedGovernanceRecords[0].Reason = AvailabilityReasonNotAuthorized
	if err := redacted.Validate(); err == nil {
		t.Fatal("redacted related record with an authorization reason must fail")
	}
}

func TestAuditControlsUseOnlyCanonicalTypedSchema(t *testing.T) {
	legacyControlFields := []string{"Control", "Outcome", "PolicyID", "PolicyRevision", "PolicyDigest", "MatchedGrant", "MatchedGrantID", "RulesDigest", "FactsDigest"}
	controlType := reflect.TypeOf(EvaluatedControl{})
	for _, field := range legacyControlFields {
		if _, exists := controlType.FieldByName(field); exists {
			t.Fatalf("deprecated EvaluatedControl.%s remains in the public schema", field)
		}
	}
	if _, exists := reflect.TypeOf(AuditEvent{}).FieldByName("AuditEventID"); exists {
		t.Fatal("deprecated AuditEvent.AuditEventID remains in the public schema")
	}

	authorization := EvaluatedControl{
		Kind: ControlAuthorization, ControlID: "policy-1", Revision: "1", Digest: "sha256:policy", Result: ControlResultAllowed,
		Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"},
	}
	admission := EvaluatedControl{
		Kind: ControlAdmission, ControlID: "admission-1", Revision: "1", Digest: "sha256:admission", Result: ControlResultAllowed,
		Admission: &AdmissionControlProvenance{
			AdmissionID: "admission-1", RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", Result: AdmissionAdmit,
			EvaluatedChecks: []AdmissionEvaluatedCheck{{Key: "capacity", Status: AdmissionCheckPass, Reason: "within limit"}}, DecisionDigest: "sha256:decision",
		},
	}
	if err := authorization.Validate(); err != nil {
		t.Fatalf("canonical authorization control rejected: %v", err)
	}
	if err := admission.Validate(); err != nil {
		t.Fatalf("canonical admission control rejected: %v", err)
	}
	for _, invalid := range []EvaluatedControl{
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "1", Digest: "sha256:policy", Result: ControlResultAllowed},
		{Kind: ControlAdmission, ControlID: "admission-1", Revision: "1", Digest: "sha256:admission", Result: ControlResultAllowed},
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "1", Digest: "sha256:policy", Result: ControlResultAllowed, Authorization: authorization.Authorization, Admission: admission.Admission},
	} {
		if err := invalid.Validate(); err == nil {
			t.Fatal("incomplete or cross-kind typed control unexpectedly validated")
		}
	}

	event := validAuditEvent("audit-canonical-controls", time.Now().UTC())
	event.EvaluatedControls = []EvaluatedControl{authorization, admission}
	if err := event.Validate(); err != nil {
		t.Fatalf("canonical typed AuditEvent rejected: %v", err)
	}
	payload, err := json.Marshal(event)
	if err != nil {
		t.Fatalf("canonical typed AuditEvent did not serialize: %v", err)
	}
	var roundTrip AuditEvent
	if err := json.Unmarshal(payload, &roundTrip); err != nil {
		t.Fatalf("canonical typed AuditEvent did not deserialize: %v", err)
	}
	if auditEventAuthorityDigest(roundTrip) != auditEventAuthorityDigest(event) {
		t.Fatal("canonical typed AuditEvent wire round-trip changed its authority digest")
	}
}

func TestAuditEventRejectsDuplicateControlIdentityButAllowsDistinctProviderChecks(t *testing.T) {
	duplicate := validAuditEvent("audit-duplicate-control", time.Now().UTC())
	duplicate.EvaluatedControls = append(duplicate.EvaluatedControls, duplicate.EvaluatedControls[0])
	if err := duplicate.Validate(); err == nil {
		t.Fatal("AuditEvent accepted a duplicate (kind, control_id) identity")
	}

	providerControl := func(id, providerID string) EvaluatedControl {
		return EvaluatedControl{
			Kind: ControlProvider, ControlID: id, Revision: "1", Digest: "sha256:control",
			Result: ControlResultSucceeded,
			Provider: &ProviderControlProvenance{
				ProviderID:     providerID,
				Release:        "provider@1.2.3@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
				ResponseDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
			},
		}
	}
	providers := validAuditEvent("audit-distinct-provider-checks", time.Now().UTC())
	providers.EvaluatedControls = []EvaluatedControl{providerControl("provider-policy-a", "provider-a"), providerControl("provider-policy-b", "provider-b")}
	if err := providers.Validate(); err != nil {
		t.Fatalf("distinct provider checks were rejected: %v", err)
	}
}

func TestAuditScopeAndCausalEvidenceAreBound(t *testing.T) {
	event := validAuditEvent("audit-causal", time.Now().UTC())
	missingCause := event
	missingCause.CausationID = ""
	if err := missingCause.Validate(); err == nil {
		t.Fatal("audit event without causation identity must fail")
	}
	summary := exportSummary(event)
	summary.CorrelationID = ""
	if err := summary.Validate(); err == nil {
		t.Fatal("audit summary without correlation identity must fail")
	}
	exported := exportEvent(event)
	exported.CausationID = ""
	if err := exported.Validate(); err == nil {
		t.Fatal("audit export projection without causation identity must fail")
	}
	target := AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "target", Description: "desc", ExportType: ExportTypeSyslog, Configuration: AuditExportConfiguration{SchemaVersion: ExportSchemaSyslogV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "tls://siem.example.test:6514"}}}, ConfigurationDigest: "sha256:target", Revision: "1"}
	update := UpdateAuditExportTargetRequest{RequestContext: testRequestContext("scope-2", "request-1"), TargetID: target.ID, ExpectedRevision: "1", Name: target.Name, Description: target.Description}
	if err := update.ValidateForTarget(target); err == nil {
		t.Fatal("target mutation with a mismatched trusted context scope must fail")
	}
}

type countingBindingResolver struct{ calls int }

func (resolver *countingBindingResolver) ResolveCredentialBinding(string) (CredentialBinding, error) {
	resolver.calls++
	return CredentialBinding{}, nil
}

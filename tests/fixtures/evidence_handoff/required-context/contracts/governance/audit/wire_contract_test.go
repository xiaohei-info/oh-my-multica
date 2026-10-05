package audit

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestAuditWireNamesAreExplicitAndStable(t *testing.T) {
	types := []reflect.Type{
		reflect.TypeOf(SubjectKey{}), reflect.TypeOf(EvaluatedControl{}), reflect.TypeOf(AuthorizationControlProvenance{}),
		reflect.TypeOf(AdmissionEvaluatedCheck{}), reflect.TypeOf(AdmissionControlProvenance{}), reflect.TypeOf(ProviderControlProvenance{}),
		reflect.TypeOf(Redaction{}), reflect.TypeOf(SafeSummaryField{}), reflect.TypeOf(SafeSummary{}), reflect.TypeOf(AuditEvent{}), reflect.TypeOf(TimeRange{}), reflect.TypeOf(AuditEventFilter{}),
		reflect.TypeOf(QueryPolicy{}), reflect.TypeOf(CursorBinding{}), reflect.TypeOf(CursorPosition{}), reflect.TypeOf(QueryAuditEventsRequest{}),
		reflect.TypeOf(AuditEventSummary{}), reflect.TypeOf(AuditEventPage{}), reflect.TypeOf(RelatedGovernanceRecord{}), reflect.TypeOf(RelatedSubject{}),
		reflect.TypeOf(CredentialUseEvidence{}), reflect.TypeOf(RequestContextSummary{}), reflect.TypeOf(EvaluatedControlAnchor{}), reflect.TypeOf(AuditEventDetailView{}),
		reflect.TypeOf(ExportAuditEventsRequest{}), reflect.TypeOf(ExportPolicy{}), reflect.TypeOf(ExportLimitExceeded{}), reflect.TypeOf(AuditExportTarget{}),
		reflect.TypeOf(ConfigurationField{}), reflect.TypeOf(AuditExportConfiguration{}), reflect.TypeOf(AuditExportTargetDraft{}), reflect.TypeOf(SaveAuditExportTargetResult{}), reflect.TypeOf(CredentialBinding{}),
		reflect.TypeOf(SaveAuditExportTargetRequest{}), reflect.TypeOf(UpdateAuditExportTargetRequest{}), reflect.TypeOf(EnableAuditExportTargetRequest{}),
		reflect.TypeOf(DisableAuditExportTargetRequest{}), reflect.TypeOf(ReplaceAuditExportTargetRequest{}), reflect.TypeOf(AuditExportProbePayload{}),
		reflect.TypeOf(AuditExportProbeRequest{}), reflect.TypeOf(AuditExportProbeResult{}), reflect.TypeOf(AuditExportBatch{}), reflect.TypeOf(AuditExportEvent{}),
		reflect.TypeOf(AuditExportStream{}), reflect.TypeOf(AuditExportChunk{}), reflect.TypeOf(AuditExportAcknowledgement{}),
	}
	for _, typ := range types {
		for index := 0; index < typ.NumField(); index++ {
			field := typ.Field(index)
			if field.PkgPath != "" { // unexported implementation state
				continue
			}
			if field.Tag.Get("json") == "" {
				t.Errorf("%s.%s has no explicit json tag", typ.Name(), field.Name)
			}
		}
	}

	configuration := AuditExportConfiguration{SchemaVersion: ExportSchemaWebhookV1, Fields: []ConfigurationField{{Name: ConfigurationFieldEndpoint, Value: "https://siem.example.test/events"}}}
	digest, err := configuration.DigestFor(ExportTypeWebhook, "binding-1")
	if err != nil {
		t.Fatal(err)
	}
	event := validAuditEvent("audit-1", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC))
	event.Subject.Digest = "sha256:subject"
	event.EvaluatedControls[0].Authorization.Decision = AuthorizationControlAllow
	payload, err := json.Marshal(struct {
		Event         AuditEvent               `json:"event"`
		Target        AuditExportTarget        `json:"target"`
		Request       QueryAuditEventsRequest  `json:"request"`
		Redaction     Redaction                `json:"redaction"`
		Configuration AuditExportConfiguration `json:"configuration"`
		Digest        string                   `json:"configuration_digest"`
		Cursor        string                   `json:"next_cursor"`
	}{
		Event:     event,
		Target:    AuditExportTarget{ID: "target-1", ScopeID: "scope-1", Name: "siem", Description: "endpoint", ExportType: ExportTypeWebhook, Configuration: configuration, ConfigurationDigest: digest, CredentialBindingID: "binding-1", Revision: "1"},
		Request:   QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: event.OccurredAt.Add(-time.Hour), End: event.OccurredAt.Add(time.Hour)}}, Cursor: "cursor", PageSize: 1},
		Redaction: Redaction{Field: "credential.secret", Reason: RedactionSecret}, Configuration: configuration, Digest: digest, Cursor: "cursor",
	})
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	anchorPayload, err := json.Marshal(exportSummary(event))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(anchorPayload), "\"evaluated_control_anchors\"") {
		t.Fatalf("wire summary missing evaluated control anchors: %s", anchorPayload)
	}
	for _, name := range []string{"\"audit_event_id\"", "\"configuration_digest\"", "\"cursor\"", "\"next_cursor\"", "\"redaction\"", "\"reason\""} {
		if !strings.Contains(text, name) {
			t.Fatalf("wire payload missing representative field %s: %s", name, text)
		}
	}
	if strings.Contains(text, "AuditEventID") || strings.Contains(text, "ConfigurationDigest") {
		t.Fatalf("wire payload used Go field names: %s", text)
	}

	var decoded struct {
		Event struct {
			ID string `json:"audit_event_id"`
		} `json:"event"`
		Target struct {
			Digest string `json:"configuration_digest"`
		} `json:"target"`
	}
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Event.ID != event.ID || decoded.Target.Digest != digest {
		t.Fatalf("wire round-trip lost stable identity: %+v", decoded)
	}
}

func TestAuthorizationControlAllowsAndDeniesWithoutAmbiguousProvenance(t *testing.T) {
	allow := EvaluatedControl{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: ControlResultAllowed, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"}}
	if err := allow.Validate(); err != nil {
		t.Fatalf("allow Validate() error = %v", err)
	}
	deny := EvaluatedControl{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: ControlResultDenied, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlDeny, ReasonCode: "missing-grant", Reason: "no matching grant"}}
	if err := deny.Validate(); err != nil {
		t.Fatalf("deny Validate() error = %v", err)
	}
	for _, invalid := range []EvaluatedControl{
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: ControlResultDenied, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlDeny, MatchedGrant: "fabricated", ReasonCode: "missing-grant"}},
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: ControlResultAllowed, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow}},
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: "pending", Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"}},
		{Kind: ControlAuthorization, ControlID: "policy-1", Revision: "7", Digest: "sha256:policy", Result: ControlResultDenied, Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"}},
	} {
		if err := invalid.Validate(); err == nil {
			t.Fatalf("ambiguous authorization control unexpectedly validated: %+v", invalid)
		}
	}
}

func TestAuditQueryCopiesSubjectPointerAndHonorsDigest(t *testing.T) {
	start := time.Date(2026, time.August, 1, 0, 0, 0, 0, time.UTC)
	subject := &SubjectKey{Kind: "project", ID: "project-1", Digest: "sha256:one"}
	request := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: start, End: start.Add(time.Hour)}, Subject: subject}, Cursor: "", PageSize: 1}
	query, err := request.ValidatedWithPolicy(DefaultQueryPolicy())
	if err != nil {
		t.Fatal(err)
	}
	// Mutating caller-owned state after validation must not change the bound query.
	subject.Digest = "sha256:two"
	if query.Filter().Subject == nil || query.Filter().Subject.Digest != "sha256:one" {
		t.Fatalf("validated query retained caller-owned subject pointer: %+v", query.Filter().Subject)
	}
	returned := query.Request()
	returned.Filter.Subject.Digest = "sha256:three"
	if query.Filter().Subject.Digest != "sha256:one" {
		t.Fatal("Request() did not return a deep copy of the subject pointer")
	}
	matching := exportSummary(validAuditEvent("audit-1", start.Add(30*time.Minute)))
	matching.Subject = SubjectKey{Kind: "project", ID: "project-1", Digest: "sha256:one"}
	if err := (AuditEventPage{Items: []AuditEventSummary{matching}}).Validate(query, DefaultQueryPolicy()); err != nil {
		t.Fatalf("matching subject digest rejected: %v", err)
	}
	wrongDigest := matching
	wrongDigest.Subject.Digest = "sha256:two"
	if err := (AuditEventPage{Items: []AuditEventSummary{wrongDigest}}).Validate(query, DefaultQueryPolicy()); err == nil {
		t.Fatal("same subject with a different digest must be rejected")
	}
	if _, err := query.NextCursor(AuditEventPage{Items: []AuditEventSummary{wrongDigest}}); err == nil {
		t.Fatal("NextCursor must reject same subject with a different digest")
	}
}

package audit

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	auditstore "github.com/xiaohei-info/open-agent-cluster/contracts/governance/audit/internal/auditstore"
	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/principal"
	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
)

func TestAuditEventValidateAcceptsRedactedImmutableEvent(t *testing.T) {
	event := AuditEvent{
		ID:               "audit-1",
		ScopeID:          "scope-1",
		RequestContextID: "request-1",
		PrincipalID:      "principal-1",
		Category:         CategoryAuthorization,
		Action:           "QueryAuditEvents",
		Subject:          SubjectKey{Kind: "project", ID: "project-1"},
		Outcome:          OutcomeAllowed,
		OccurredAt:       time.Date(2026, time.July, 29, 12, 0, 0, 0, time.UTC),
		CausationID:      "cause-1",
		CorrelationID:    "corr-1",
		EvaluatedControls: []EvaluatedControl{{
			Kind:          ControlAuthorization,
			ControlID:     "policy-1",
			Revision:      "7",
			Digest:        "sha256:policy",
			Result:        ControlResultAllowed,
			Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"},
		}},
		Redactions: []Redaction{{Field: "credential.secret", Reason: RedactionSecret}},
	}

	if err := event.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestQueryAuditEventsRequestValidatesBoundedStablePagination(t *testing.T) {
	request := QueryAuditEventsRequest{
		RequestContext: testRequestContext("scope-1", "request-1"),
		Filter: AuditEventFilter{TimeRange: TimeRange{
			Start: time.Date(2026, time.July, 28, 0, 0, 0, 0, time.UTC),
			End:   time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
		}},
		Cursor:   "",
		PageSize: 50,
	}

	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if AuditEventSortOrder != "occurred_at DESC, audit_event_id DESC" {
		t.Fatalf("AuditEventSortOrder = %q", AuditEventSortOrder)
	}

	request.Filter.TimeRange.End = request.Filter.TimeRange.Start
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected empty time range")
	}
}

func TestAuditExportBatchValidateRejectsUnstableOrder(t *testing.T) {
	first := validAuditEvent("audit-2", time.Date(2026, time.July, 29, 12, 0, 0, 0, time.UTC))
	second := validAuditEvent("audit-1", time.Date(2026, time.July, 29, 12, 0, 0, 0, time.UTC))
	batch := AuditExportBatch{
		TargetID:                  "target-1",
		TargetScopeID:             "scope-1",
		TargetConfigurationDigest: "sha256:target",
		DeliveryID:                "delivery-1",
		StartCursor:               "cursor-audit-2",
		EndCursor:                 "cursor-audit-1",
		Events:                    []AuditExportEvent{exportEvent(first), exportEvent(second)},
		CursorInterval:            []string{"cursor-audit-2", "cursor-audit-1"},
	}
	if err := batch.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}

	batch.Events[0], batch.Events[1] = batch.Events[1], batch.Events[0]
	if err := batch.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected non-descending events")
	}

	newest := validAuditEvent("audit-3", time.Date(2026, time.July, 29, 12, 1, 0, 0, time.UTC))
	batch.Events = []AuditExportEvent{exportEvent(newest), exportEvent(first)}
	batch.CursorInterval = []string{"cursor-audit-3", "cursor-audit-2"}
	batch.StartCursor, batch.EndCursor = "cursor-audit-3", "cursor-audit-2"
	if err := batch.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestExportAuditEventsRequestRequiresSupportedFormat(t *testing.T) {
	request := ExportAuditEventsRequest{
		RequestContext: testRequestContext("scope-1", "request-1"),
		Filter: AuditEventFilter{TimeRange: TimeRange{
			Start: time.Date(2026, time.July, 28, 0, 0, 0, 0, time.UTC),
			End:   time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
		}},
		Format: ExportFormatJSONL,
	}
	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	request.Format = "xml"
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected export format")
	}
}

func TestAuditContractsRejectInvalidBoundaryInputs(t *testing.T) {
	tests := []struct {
		name  string
		event AuditEvent
	}{
		{name: "category", event: AuditEvent{ID: "audit-1", ScopeID: "scope-1", RequestContextID: "request-1", Category: "unknown", Action: "act", Subject: SubjectKey{Kind: "project", ID: "project-1"}, Outcome: OutcomeAllowed, OccurredAt: time.Now()}},
		{name: "subject", event: AuditEvent{ID: "audit-1", ScopeID: "scope-1", RequestContextID: "request-1", Category: CategoryAuthorization, Action: "act", Outcome: OutcomeAllowed, OccurredAt: time.Now()}},
		{name: "control", event: AuditEvent{ID: "audit-1", ScopeID: "scope-1", RequestContextID: "request-1", Category: CategoryAuthorization, Action: "act", Subject: SubjectKey{Kind: "project", ID: "project-1"}, Outcome: OutcomeAllowed, OccurredAt: time.Now(), EvaluatedControls: []EvaluatedControl{{}}}},
		{name: "redaction", event: AuditEvent{ID: "audit-1", ScopeID: "scope-1", RequestContextID: "request-1", Category: CategoryAuthorization, Action: "act", Subject: SubjectKey{Kind: "project", ID: "project-1"}, Outcome: OutcomeAllowed, OccurredAt: time.Now(), Redactions: []Redaction{{Field: "secret", Reason: "unknown"}}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.event.Validate(); err == nil {
				t.Fatal("Validate() error = nil, want rejected event")
			}
		})
	}

	validQuery := QueryAuditEventsRequest{RequestContext: testRequestContext("scope-1", "request-1"), Filter: AuditEventFilter{TimeRange: TimeRange{Start: time.Date(2026, time.July, 28, 0, 0, 0, 0, time.UTC), End: time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC)}}, PageSize: 1}
	for _, mutate := range []func(*QueryAuditEventsRequest){
		func(request *QueryAuditEventsRequest) { request.Filter.Category = "unknown" },
		func(request *QueryAuditEventsRequest) { request.Filter.Outcome = "unknown" },
		func(request *QueryAuditEventsRequest) { request.Filter.Subject = &SubjectKey{Kind: "project"} },
	} {
		request := validQuery
		mutate(&request)
		if err := request.Validate(); err == nil {
			t.Fatal("Validate() error = nil, want rejected query filter")
		}
	}
}

func TestAuditExportBatchRejectsMissingOrInvalidEvents(t *testing.T) {
	if err := (AuditExportBatch{}).Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected empty batch")
	}
	batch := AuditExportBatch{TargetID: "target-1", TargetScopeID: "scope-1", TargetConfigurationDigest: "sha256:target", DeliveryID: "delivery-1", StartCursor: "start", EndCursor: "end", Events: []AuditExportEvent{{}}, CursorInterval: []string{"end"}}
	if err := batch.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want rejected invalid event")
	}
}

func testRequestContext(scopeID, requestID string) requestcontext.RequestContext {
	return requestcontext.RequestContext{
		PrincipalIdentity: principal.PrincipalIdentity{PrincipalID: "principal-1"},
		ScopeIdentity:     requestcontext.ScopeIdentity{ScopeID: scopeID},
		RequestID:         requestID,
		CausationID:       "cause-" + requestID,
		CorrelationID:     "corr-" + requestID,
		Authentication: requestcontext.AuthenticationContext{
			AuthenticationMethodID: "method-1", AuthenticationDriver: "test", ExternalIdentityLinkID: "external-1", AuthenticationStrength: "strong", AuthenticatedAt: time.Date(2026, time.July, 29, 11, 0, 0, 0, time.UTC),
		},
		Source: "test", PolicyContextRevision: 1, PolicyContextDigest: "sha256:context",
	}
}

func validAuditEvent(id string, occurredAt time.Time) AuditEvent {
	return AuditEvent{
		ID:               id,
		ScopeID:          "scope-1",
		RequestContextID: "request-1",
		PrincipalID:      "principal-1",
		Category:         CategoryStateWrite,
		Action:           "UpdateProject",
		Subject:          SubjectKey{Kind: "project", ID: "project-1"},
		Outcome:          OutcomeSucceeded,
		OccurredAt:       occurredAt,
		CausationID:      "cause-" + id,
		CorrelationID:    "corr-" + id,
		EvaluatedControls: []EvaluatedControl{{
			Kind:          ControlAuthorization,
			ControlID:     "policy-1",
			Revision:      "1",
			Digest:        "sha256:policy",
			Result:        ControlResultAllowed,
			Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"},
		}},
	}
}

func exportEvent(event AuditEvent) AuditExportEvent {
	return AuditExportEvent{ID: event.ID, ScopeID: event.ScopeID, ProjectID: event.ProjectID, PrincipalID: event.PrincipalID, Subject: event.Subject, Category: event.Category, Action: event.Action, Outcome: event.Outcome, OccurredAt: event.OccurredAt, CausationID: event.CausationID, CorrelationID: event.CorrelationID, Cursor: "cursor-" + event.ID, Redactions: event.Redactions}
}

func exportPayload(format ExportFormat, events []AuditExportEvent) []byte {
	payload, err := CanonicalAuditExportPayload(format, events)
	if err != nil {
		panic(err)
	}
	return payload
}

func signedAuditEventStore(event AuditEvent) (AuditEventStoreCapability, error) {
	if err := event.Validate(); err != nil {
		return AuditEventStoreCapability{}, err
	}
	payload, err := json.Marshal(event)
	if err != nil {
		return AuditEventStoreCapability{}, err
	}
	return NewAuditEventStoreCapability(auditstore.NewReceipt(event.ID, payload)), nil
}

func issueTestAuditEventStoreBinding(event AuditEvent) (AuditEventStoreBinding, error) {
	store, err := signedAuditEventStore(event)
	if err != nil {
		return AuditEventStoreBinding{}, err
	}
	return LoadAuditEventStoreBinding(context.Background(), store, event.ID)
}

func exportSummary(event AuditEvent) AuditEventSummary {
	binding, err := issueTestAuditEventStoreBinding(event)
	if err != nil {
		panic(err)
	}
	detail, err := NewAuditEventDetailViewFromStore(binding)
	if err != nil {
		panic(err)
	}
	return detail.Event
}

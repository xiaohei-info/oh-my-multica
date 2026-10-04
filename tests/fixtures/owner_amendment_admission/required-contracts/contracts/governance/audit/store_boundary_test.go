package audit

import (
	"context"
	"testing"
	"time"
)

func TestAuditStoreCapabilityAdapterPathCanIssueVerifiedBindingInternal(t *testing.T) {
	event := AuditEvent{
		ID:               "audit-external-store",
		ScopeID:          "scope-1",
		RequestContextID: "request-1",
		PrincipalID:      "principal-1",
		Category:         CategoryStateWrite,
		Action:           "update_project",
		Subject:          SubjectKey{Kind: "project", ID: "project-1"},
		Outcome:          OutcomeSucceeded,
		OccurredAt:       time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC),
		CausationID:      "cause-1",
		CorrelationID:    "corr-1",
		EvaluatedControls: []EvaluatedControl{{
			Kind:          ControlAuthorization,
			ControlID:     "policy-1",
			Revision:      "1",
			Digest:        "sha256:policy",
			Result:        ControlResultAllowed,
			Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"},
		}},
	}
	store, err := signedAuditEventStore(event)
	if err != nil {
		t.Fatalf("signedAuditEventStore() error = %v", err)
	}
	bound, err := LoadAuditEventStoreBinding(context.Background(), store, event.ID)
	if err != nil {
		t.Fatalf("LoadAuditEventStoreBinding() error = %v", err)
	}
	view, err := NewAuditEventDetailViewFromStore(bound)
	if err != nil {
		t.Fatalf("NewAuditEventDetailViewFromStore() error = %v", err)
	}
	view.RequestContextSummary = RequestContextSummary{PrincipalID: event.PrincipalID, ScopeID: event.ScopeID, Source: "api", RequestContextID: event.RequestContextID, RequestID: "request-http-1", CausationID: event.CausationID, CorrelationID: event.CorrelationID}
	if err := view.Validate(); err != nil {
		t.Fatalf("store-bound detail Validate() error = %v", err)
	}
}

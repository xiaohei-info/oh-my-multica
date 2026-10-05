package audit

import (
	"testing"
	"time"
)

func TestAuditRejectsUnknownControlOutcome(t *testing.T) {
	event := AuditEvent{
		ID:               "audit-1",
		ScopeID:          "scope-1",
		RequestContextID: "request-1",
		PrincipalID:      "principal-1",
		Category:         CategoryCredential,
		Action:           "credential.resolve",
		Subject:          SubjectKey{Kind: "credential-binding", ID: "binding-1", Digest: "sha256:subject"},
		Outcome:          OutcomeSucceeded,
		CausationID:      "cause-1",
		CorrelationID:    "corr-1",
		EvaluatedControls: []EvaluatedControl{{
			Kind:          ControlAuthorization,
			ControlID:     "policy-1",
			Revision:      "1",
			Digest:        "sha256:policy",
			Result:        "mystery",
			Authorization: &AuthorizationControlProvenance{Decision: AuthorizationControlAllow, MatchedGrant: "grant-1"},
		}},
		OccurredAt: time.Now().UTC(),
	}
	if err := event.Validate(); err == nil {
		t.Fatal("unknown authorization control outcome accepted")
	}
	event.EvaluatedControls[0].Result = ControlResultDenied
	event.EvaluatedControls[0].Authorization = &AuthorizationControlProvenance{Decision: AuthorizationControlDeny, ReasonCode: "denied"}
	if err := event.Validate(); err != nil {
		t.Fatalf("closed denied authorization outcome rejected: %v", err)
	}
}

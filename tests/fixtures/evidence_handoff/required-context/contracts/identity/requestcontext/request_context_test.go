package requestcontext

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/principal"
)

func TestRequestContextRequiresAuthenticatedGovernanceFacts(t *testing.T) {
	context := validRequestContext()

	if err := context.Validate(); err != nil {
		t.Fatalf("Validate() returned %v", err)
	}

	context.Authentication.ExternalIdentityLinkID = ""
	if err := context.Validate(); err == nil {
		t.Fatal("Validate() accepted an incomplete authentication context")
	}
}

func TestRequestContextPublishesFlatPrincipalAndScopeJSON(t *testing.T) {
	context := validRequestContext()

	encoded, err := json.Marshal(context)
	if err != nil {
		t.Fatalf("Marshal() returned %v", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatalf("Unmarshal() returned %v", err)
	}
	for _, key := range []string{"principal_id", "scope_id"} {
		if _, ok := fields[key]; !ok {
			t.Fatalf("JSON has no top-level %q: %s", key, encoded)
		}
	}
	for _, key := range []string{"principal", "scope"} {
		if _, ok := fields[key]; ok {
			t.Fatalf("JSON unexpectedly has nested %q: %s", key, encoded)
		}
	}

	var decoded RequestContext
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("Unmarshal() returned %v", err)
	}
	if !reflect.DeepEqual(decoded, context) {
		t.Fatalf("round trip = %#v, want %#v", decoded, context)
	}
}

func TestRequestContextRejectsLegacyNestedIdentityJSON(t *testing.T) {
	for _, encoded := range []string{
		`{"principal":{"principal_id":"principal-123"}}`,
		`{"scope":{"scope_id":"scope-local"}}`,
	} {
		var context RequestContext
		if err := json.Unmarshal([]byte(encoded), &context); err == nil {
			t.Fatalf("Unmarshal() accepted nested identity JSON: %s", encoded)
		}
	}
}

func TestRequestContextRejectsMalformedMistypedAndIncompleteJSON(t *testing.T) {
	for _, encoded := range []string{
		`{`,
		`{"principal_id":"principal-123","scope_id":"scope-local","policy_context_revision":"seven"}`,
		`{"principal_id":"principal-123","scope_id":"scope-local"}`,
	} {
		var context RequestContext
		if err := json.Unmarshal([]byte(encoded), &context); err == nil {
			t.Fatalf("Unmarshal() accepted invalid JSON: %s", encoded)
		}
	}
}

func validRequestContext() RequestContext {
	now := time.Date(2026, time.July, 29, 12, 0, 0, 0, time.UTC)
	return RequestContext{
		PrincipalIdentity: principal.PrincipalIdentity{PrincipalID: "principal-123"},
		ScopeIdentity:     ScopeIdentity{ScopeID: "scope-local"},
		RequestID:         "request-123",
		CausationID:       "cause-123",
		CorrelationID:     "correlation-123",
		Authentication: AuthenticationContext{
			AuthenticationMethodID: "method-local",
			AuthenticationDriver:   "extension-local@1.0.0#digest",
			ExternalIdentityLinkID: "link-123",
			AuthenticationStrength: "password",
			AuthenticatedAt:        now,
		},
		Source:                "web",
		PolicyContextRevision: 7,
		PolicyContextDigest:   "sha256:policy",
	}
}

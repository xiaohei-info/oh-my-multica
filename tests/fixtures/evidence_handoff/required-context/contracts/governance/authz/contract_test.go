package authz

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/principal"
	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
	"github.com/xiaohei-info/open-agent-cluster/contracts/kernel"
)

func TestAuthorizationPolicyValidateAcceptsAllowOnlyProfiles(t *testing.T) {
	if err := validPolicy().Validate(); err != nil {
		t.Fatalf("validate policy: %v", err)
	}
}

func TestAuthorizationPolicyValidateRejectsInvalidPolicyShapes(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*AuthorizationPolicy)
	}{
		{"missing policy identity", func(policy *AuthorizationPolicy) { policy.AuthorizationPolicyID = "" }},
		{"missing policy description", func(policy *AuthorizationPolicy) { policy.Description = "" }},
		{"no access profiles", func(policy *AuthorizationPolicy) { policy.AccessProfiles = nil }},
		{"profile missing name", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Name = "" }},
		{"profile missing description", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Description = "" }},
		{"default profile missing", func(policy *AuthorizationPolicy) { policy.DefaultAccessProfileKey = "missing" }},
		{"duplicate profile key", func(policy *AuthorizationPolicy) {
			policy.AccessProfiles = append(policy.AccessProfiles, policy.AccessProfiles[0])
		}},
		{"no grants", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants = nil }},
		{"grant missing key", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].GrantKey = "" }},
		{"grant missing description", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].Description = "" }},
		{"duplicate grant key", func(policy *AuthorizationPolicy) {
			policy.AccessProfiles[0].Grants = append(policy.AccessProfiles[0].Grants, policy.AccessProfiles[0].Grants[0])
		}},
		{"no actions", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].ActionKeys = nil }},
		{"empty action", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].ActionKeys = []string{""} }},
		{"no subject kinds", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].SubjectKinds = nil }},
		{"empty subject kind", func(policy *AuthorizationPolicy) { policy.AccessProfiles[0].Grants[0].SubjectKinds = []string{""} }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			policy := validPolicy()
			test.mutate(&policy)
			if err := policy.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
}

func TestAuthorizationRequestValidateRequiresExactGovernedInput(t *testing.T) {
	if err := validRequest().Validate(); err != nil {
		t.Fatalf("validate request: %v", err)
	}

	request := validRequest()
	request.RequestContext.PrincipalID = ""
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() succeeded with an incomplete request context")
	}

	request = validRequest()
	request.RequestedScopeID = "other-scope"
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() accepted a requested scope outside the request context")
	}

	request = validRequest()
	request.Subject.Digest = ""
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() succeeded without subject digest")
	}
}

func TestAuthorizationRequestValidateRejectsMalformedExplicitFacts(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*AuthorizationRequest)
	}{
		{"attribute missing key", func(request *AuthorizationRequest) { request.Attributes = []Attribute{{Value: "internal"}} }},
		{"attribute missing value", func(request *AuthorizationRequest) { request.Attributes = []Attribute{{Key: "data_classification"}} }},
		{"duplicate attribute key", func(request *AuthorizationRequest) {
			request.Attributes = []Attribute{{Key: "data_classification", Value: "internal"}, {Key: "data_classification", Value: "restricted"}}
		}},
		{"requirement missing key", func(request *AuthorizationRequest) { request.Requirements = []Requirement{{Value: "human"}} }},
		{"requirement missing value", func(request *AuthorizationRequest) { request.Requirements = []Requirement{{Key: "responsibility"}} }},
		{"duplicate requirement key", func(request *AuthorizationRequest) {
			request.Requirements = []Requirement{{Key: "responsibility", Value: "human"}, {Key: "responsibility", Value: "independent-reviewer"}}
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := validRequest()
			test.mutate(&request)
			if err := request.Validate(); err == nil {
				t.Fatal("Validate() accepted malformed explicit facts")
			}
		})
	}
}

func TestAuthorizationContractsPublishSnakeCaseWireFields(t *testing.T) {
	encoded, err := json.Marshal(validPolicy())
	if err != nil {
		t.Fatalf("Marshal() returned %v", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatalf("Unmarshal() returned %v", err)
	}
	if _, found := fields["authorization_policy_id"]; !found {
		t.Fatalf("policy JSON has no authorization_policy_id: %s", encoded)
	}
	if _, found := fields["AuthorizationPolicyID"]; found {
		t.Fatalf("policy JSON exposed Go field name: %s", encoded)
	}
	var decodedPolicy AuthorizationPolicy
	if err := json.Unmarshal(encoded, &decodedPolicy); err != nil {
		t.Fatalf("Unmarshal policy returned %v", err)
	}
	if !reflect.DeepEqual(decodedPolicy, validPolicy()) {
		t.Fatalf("policy round trip = %#v, want %#v", decodedPolicy, validPolicy())
	}

	encoded, err = json.Marshal(validRequest())
	if err != nil {
		t.Fatalf("Marshal request returned %v", err)
	}
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatalf("Unmarshal request returned %v", err)
	}
	for _, key := range []string{"request_context", "action_key", "requested_scope", "evaluated_at"} {
		if _, found := fields[key]; !found {
			t.Fatalf("request JSON has no %q: %s", key, encoded)
		}
	}
	if _, found := fields["requested_scope_id"]; found {
		t.Fatalf("request JSON exposed non-authoritative requested_scope_id: %s", encoded)
	}
	for _, key := range []string{"attributes", "requirements"} {
		if _, found := fields[key]; found {
			t.Fatalf("request JSON unexpectedly includes empty optional %q: %s", key, encoded)
		}
	}
	var decodedRequest AuthorizationRequest
	if err := json.Unmarshal(encoded, &decodedRequest); err != nil {
		t.Fatalf("Unmarshal request returned %v", err)
	}
	if !reflect.DeepEqual(decodedRequest, validRequest()) {
		t.Fatalf("request round trip = %#v, want %#v", decodedRequest, validRequest())
	}
}

func TestAuthorizationDecisionValidateRequiresKnownEffectAndPolicyProvenance(t *testing.T) {
	if err := validDecision().Validate(); err != nil {
		t.Fatalf("validate decision: %v", err)
	}

	decision := validDecision()
	decision.Effect = "abstain"
	if err := decision.Validate(); err == nil {
		t.Fatal("Validate() succeeded for abstain")
	}

	decision = validDecision()
	decision.MatchedGrants[0].GrantKey = ""
	if err := decision.Validate(); err == nil {
		t.Fatal("Validate() succeeded with malformed matched grant")
	}

	decision = validDecision()
	decision.MatchedGrants = nil
	if err := decision.Validate(); err == nil {
		t.Fatal("Validate() allowed an allow decision without grant provenance")
	}

	decision = validDecision()
	decision.Effect = AuthorizationEffectDeny
	decision.MatchedGrants = nil
	if err := decision.Validate(); err != nil {
		t.Fatalf("validate deny decision without matched grants: %v", err)
	}

	decision = validDecision()
	decision.Obligations = []Obligation{{Value: "restricted"}}
	if err := decision.Validate(); err == nil {
		t.Fatal("Validate() succeeded with malformed obligation")
	}
}

func TestAuthorizationEffectValues(t *testing.T) {
	if !AuthorizationEffectAllow.Valid() || !AuthorizationEffectDeny.Valid() {
		t.Fatal("published authorization effects are invalid")
	}
	if AuthorizationEffect("unknown").Valid() {
		t.Fatal("unknown effect is valid")
	}
}

func validPolicy() AuthorizationPolicy {
	return AuthorizationPolicy{
		AuthorizationPolicyID:   "policy-1",
		ScopeID:                 "scope-1",
		Name:                    "default",
		Description:             "Default scope policy.",
		DefaultAccessProfileKey: "member",
		Revision:                1,
		PolicyDigest:            "sha256:policy",
		AccessProfiles: []AccessProfile{{
			AccessProfileKey: "member",
			Name:             "Member",
			Description:      "Standard member access.",
			Grants: []Grant{{
				GrantKey:     "project-read",
				Description:  "Read projects in the scope.",
				ActionKeys:   []string{"project.read"},
				SubjectKinds: []string{"project"},
			}},
		}},
	}
}

func validRequest() AuthorizationRequest {
	return AuthorizationRequest{
		RequestContext:   validRequestContext(),
		ActionKey:        "project.read",
		Subject:          kernel.SubjectKey{Kind: "project", ID: "project-1", Digest: "sha256:project"},
		RequestedScopeID: "scope-1",
		EvaluatedAt:      time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
	}
}

func validRequestContext() requestcontext.RequestContext {
	return requestcontext.RequestContext{
		PrincipalIdentity: principal.PrincipalIdentity{PrincipalID: "principal-1"},
		ScopeIdentity:     requestcontext.ScopeIdentity{ScopeID: "scope-1"},
		RequestID:         "request-1",
		CausationID:       "causation-1",
		CorrelationID:     "correlation-1",
		Authentication: requestcontext.AuthenticationContext{
			AuthenticationMethodID: "method-1",
			AuthenticationDriver:   "driver-1",
			ExternalIdentityLinkID: "link-1",
			AuthenticationStrength: "mfa",
			AuthenticatedAt:        time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
		},
		Source:                "web",
		PolicyContextRevision: 1,
		PolicyContextDigest:   "sha256:policy-context",
	}
}

func validDecision() AuthorizationDecision {
	return AuthorizationDecision{
		AuthorizationDecisionID: "authorization-decision-1",
		RequestDigest:           "sha256:request",
		Effect:                  AuthorizationEffectAllow,
		ReasonCode:              "grant-matched",
		AuthorizationPolicyID:   "policy-1",
		PolicyRevision:          1,
		PolicyDigest:            "sha256:policy",
		MatchedGrants:           []MatchedGrant{{AccessProfileKey: "member", GrantKey: "project-read"}},
		EvaluatedAt:             time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
	}
}

// Package authz publishes governance authorization contracts.
package authz

import (
	"fmt"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
	"github.com/xiaohei-info/open-agent-cluster/contracts/kernel"
)

type AuthorizationEffect string

const (
	AuthorizationEffectAllow AuthorizationEffect = "allow"
	AuthorizationEffectDeny  AuthorizationEffect = "deny"
)

func (effect AuthorizationEffect) Valid() bool {
	return effect == AuthorizationEffectAllow || effect == AuthorizationEffectDeny
}

// AuthorizationPolicy is the one mutable policy for a Scope.
type AuthorizationPolicy struct {
	AuthorizationPolicyID   string          `json:"authorization_policy_id"`
	ScopeID                 string          `json:"scope_id"`
	Name                    string          `json:"name"`
	Description             string          `json:"description"`
	DefaultAccessProfileKey string          `json:"default_access_profile_key"`
	AccessProfiles          []AccessProfile `json:"access_profiles"`
	Revision                int64           `json:"revision"`
	PolicyDigest            string          `json:"policy_digest"`
}

type AccessProfile struct {
	AccessProfileKey string  `json:"access_profile_key"`
	Name             string  `json:"name"`
	Description      string  `json:"description"`
	Grants           []Grant `json:"grants"`
}

// Grant is allow-only; an absent matching grant results in denial.
type Grant struct {
	GrantKey     string   `json:"grant_key"`
	Description  string   `json:"description"`
	ActionKeys   []string `json:"action_keys"`
	SubjectKinds []string `json:"subject_kinds"`
}

func (policy AuthorizationPolicy) Validate() error {
	if policy.AuthorizationPolicyID == "" || policy.ScopeID == "" || policy.Name == "" || policy.Description == "" || policy.DefaultAccessProfileKey == "" || policy.Revision < 1 || policy.PolicyDigest == "" {
		return fmt.Errorf("policy identity, scope, name, description, default access profile, revision, and digest are required")
	}
	if len(policy.AccessProfiles) == 0 {
		return fmt.Errorf("policy must contain access profiles")
	}

	profiles := make(map[string]struct{}, len(policy.AccessProfiles))
	defaultProfileFound := false
	for _, profile := range policy.AccessProfiles {
		if profile.AccessProfileKey == "" || profile.Name == "" || profile.Description == "" {
			return fmt.Errorf("access profile key, name, and description are required")
		}
		if _, exists := profiles[profile.AccessProfileKey]; exists {
			return fmt.Errorf("duplicate access profile key %q", profile.AccessProfileKey)
		}
		profiles[profile.AccessProfileKey] = struct{}{}
		if profile.AccessProfileKey == policy.DefaultAccessProfileKey {
			defaultProfileFound = true
		}
		if err := validateGrants(profile.Grants); err != nil {
			return err
		}
	}
	if !defaultProfileFound {
		return fmt.Errorf("default access profile %q is not declared", policy.DefaultAccessProfileKey)
	}
	return nil
}

func validateGrants(grants []Grant) error {
	if len(grants) == 0 {
		return fmt.Errorf("access profile must contain grants")
	}
	keys := make(map[string]struct{}, len(grants))
	for _, grant := range grants {
		if grant.GrantKey == "" || grant.Description == "" {
			return fmt.Errorf("grant key and description are required")
		}
		if _, exists := keys[grant.GrantKey]; exists {
			return fmt.Errorf("duplicate grant key %q", grant.GrantKey)
		}
		keys[grant.GrantKey] = struct{}{}
		if err := validateKeys("action", grant.ActionKeys); err != nil {
			return err
		}
		if err := validateKeys("subject kind", grant.SubjectKinds); err != nil {
			return err
		}
	}
	return nil
}

func validateKeys(kind string, keys []string) error {
	if len(keys) == 0 {
		return fmt.Errorf("%s keys are required", kind)
	}
	for _, key := range keys {
		if key == "" {
			return fmt.Errorf("%s key is required", kind)
		}
	}
	return nil
}

// AuthorizationRequest is the explicit input to the single AuthorizationEngine.
type AuthorizationRequest struct {
	RequestContext   requestcontext.RequestContext `json:"request_context"`
	ProjectID        string                        `json:"project_id,omitempty"`
	ActionKey        string                        `json:"action_key"`
	Subject          kernel.SubjectKey             `json:"subject"`
	RequestedScopeID string                        `json:"requested_scope"`
	Attributes       []Attribute                   `json:"attributes,omitempty"`
	Requirements     []Requirement                 `json:"requirements,omitempty"`
	EvaluatedAt      time.Time                     `json:"evaluated_at"`
}

type Attribute struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

type Requirement struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

func (request AuthorizationRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil {
		return fmt.Errorf("request context: %w", err)
	}
	if request.ActionKey == "" || request.RequestedScopeID == "" || request.EvaluatedAt.IsZero() {
		return fmt.Errorf("request context, action, requested scope, and evaluation time are required")
	}
	if request.RequestContext.ScopeID != request.RequestedScopeID {
		return fmt.Errorf("requested scope must match request context scope")
	}
	if err := request.Subject.Validate(); err != nil {
		return err
	}
	if err := validateAttributes(request.Attributes); err != nil {
		return err
	}
	return validateRequirements(request.Requirements)
}

func validateAttributes(attributes []Attribute) error {
	keys := make(map[string]struct{}, len(attributes))
	for _, attribute := range attributes {
		if attribute.Key == "" || attribute.Value == "" {
			return fmt.Errorf("attribute key and value are required")
		}
		if _, exists := keys[attribute.Key]; exists {
			return fmt.Errorf("duplicate attribute key %q", attribute.Key)
		}
		keys[attribute.Key] = struct{}{}
	}
	return nil
}

func validateRequirements(requirements []Requirement) error {
	keys := make(map[string]struct{}, len(requirements))
	for _, requirement := range requirements {
		if requirement.Key == "" || requirement.Value == "" {
			return fmt.Errorf("requirement key and value are required")
		}
		if _, exists := keys[requirement.Key]; exists {
			return fmt.Errorf("duplicate requirement key %q", requirement.Key)
		}
		keys[requirement.Key] = struct{}{}
	}
	return nil
}

// AuthorizationDecision is immutable policy provenance for one request digest.
type AuthorizationDecision struct {
	AuthorizationDecisionID string              `json:"authorization_decision_id"`
	RequestDigest           string              `json:"request_digest"`
	Effect                  AuthorizationEffect `json:"effect"`
	ReasonCode              string              `json:"reason_code"`
	AuthorizationPolicyID   string              `json:"authorization_policy_id"`
	PolicyRevision          int64               `json:"policy_revision"`
	PolicyDigest            string              `json:"policy_digest"`
	MatchedGrants           []MatchedGrant      `json:"matched_grants,omitempty"`
	Obligations             []Obligation        `json:"obligations,omitempty"`
	EvaluatedAt             time.Time           `json:"evaluated_at"`
}

type MatchedGrant struct {
	AccessProfileKey string `json:"access_profile_key"`
	GrantKey         string `json:"grant_key"`
}

type Obligation struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

func (decision AuthorizationDecision) Validate() error {
	if decision.AuthorizationDecisionID == "" || decision.RequestDigest == "" || !decision.Effect.Valid() || decision.ReasonCode == "" || decision.AuthorizationPolicyID == "" || decision.PolicyRevision < 1 || decision.PolicyDigest == "" || decision.EvaluatedAt.IsZero() {
		return fmt.Errorf("decision identity, request digest, effect, reason, policy provenance, and evaluation time are required")
	}
	if decision.Effect == AuthorizationEffectAllow && len(decision.MatchedGrants) == 0 {
		return fmt.Errorf("allow decision requires matched grant provenance")
	}
	matchedGrants := make(map[string]struct{}, len(decision.MatchedGrants))
	for _, grant := range decision.MatchedGrants {
		if grant.AccessProfileKey == "" || grant.GrantKey == "" {
			return fmt.Errorf("matched grants require access profile and grant keys")
		}
		key := grant.AccessProfileKey + "\x00" + grant.GrantKey
		if _, exists := matchedGrants[key]; exists {
			return fmt.Errorf("duplicate matched grant provenance")
		}
		matchedGrants[key] = struct{}{}
	}
	for _, obligation := range decision.Obligations {
		if obligation.Key == "" {
			return fmt.Errorf("obligation key is required")
		}
	}
	return nil
}

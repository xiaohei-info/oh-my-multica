// Package requestcontext publishes the governance context attached to requests.
package requestcontext

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/principal"
)

var ErrIncompleteRequestContext = errors.New("request context is incomplete")

var ErrNestedIdentityContext = errors.New("request context must use top-level principal_id and scope_id")

// ScopeIdentity identifies the governance scope in which a request runs.
type ScopeIdentity struct {
	ScopeID string `json:"scope_id"`
}

// AuthenticationContext records the verified authentication facts used to
// construct a RequestContext. It never carries credentials or raw identity
// material.
type AuthenticationContext struct {
	AuthenticationMethodID string    `json:"authentication_method_id"`
	AuthenticationDriver   string    `json:"authentication_driver"`
	ExternalIdentityLinkID string    `json:"external_identity_link_id"`
	AuthenticationStrength string    `json:"authentication_strength"`
	AuthenticatedAt        time.Time `json:"authenticated_at"`
}

// RequestContext is the typed OAC governance context for authenticated
// external requests and internal application commands.
type RequestContext struct {
	principal.PrincipalIdentity
	ScopeIdentity
	RequestID             string                `json:"request_id"`
	CausationID           string                `json:"causation_id"`
	CorrelationID         string                `json:"correlation_id"`
	Authentication        AuthenticationContext `json:"authentication_context"`
	Source                string                `json:"source"`
	PolicyContextRevision int64                 `json:"policy_context_revision"`
	PolicyContextDigest   string                `json:"policy_context_digest"`
}

// Validate confirms that the context contains all required governance facts.
func (context RequestContext) Validate() error {
	if !context.isComplete() {
		return ErrIncompleteRequestContext
	}
	return nil
}

func (context RequestContext) isComplete() bool {
	return context.PrincipalIdentity.Validate() == nil && context.ScopeIdentity.ScopeID != "" && context.RequestID != "" && context.CausationID != "" && context.CorrelationID != "" && context.Authentication.AuthenticationMethodID != "" && context.Authentication.AuthenticationDriver != "" && context.Authentication.ExternalIdentityLinkID != "" && context.Authentication.AuthenticationStrength != "" && !context.Authentication.AuthenticatedAt.IsZero() && context.Source != "" && context.PolicyContextRevision >= 0 && context.PolicyContextDigest != ""
}

// UnmarshalJSON accepts the authoritative flat wire shape and rejects the
// previously unpublished nested principal/scope representation.
func (context *RequestContext) UnmarshalJSON(data []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	if _, found := fields["principal"]; found {
		return ErrNestedIdentityContext
	}
	if _, found := fields["scope"]; found {
		return ErrNestedIdentityContext
	}

	type wireRequestContext RequestContext
	var decoded wireRequestContext
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	requestContext := RequestContext(decoded)
	if err := requestContext.Validate(); err != nil {
		return err
	}
	*context = requestContext
	return nil
}

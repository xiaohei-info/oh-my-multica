// Package externalidentitylink publishes OAC's exact external identity mapping.
package externalidentitylink

import "time"

// ExternalIdentityLink maps one exact authentication-method, issuer, and
// subject tuple to a local OAC principal.
type ExternalIdentityLink struct {
	ExternalIdentityLinkID string     `json:"external_identity_link_id"`
	AuthenticationMethodID string     `json:"authentication_method_id"`
	IssuerKey              string     `json:"issuer_key"`
	SubjectKey             string     `json:"subject_key"`
	PrincipalID            string     `json:"principal_id"`
	LinkedAt               time.Time  `json:"linked_at"`
	DisabledAt             *time.Time `json:"disabled_at,omitempty"`
}

// Matches reports whether this active link resolves the exact authenticated
// identity tuple. Callers must reject identities without such a match.
func (link ExternalIdentityLink) Matches(authenticationMethodID, issuerKey, subjectKey string) bool {
	return link.DisabledAt == nil && link.AuthenticationMethodID == authenticationMethodID && link.IssuerKey == issuerKey && link.SubjectKey == subjectKey
}

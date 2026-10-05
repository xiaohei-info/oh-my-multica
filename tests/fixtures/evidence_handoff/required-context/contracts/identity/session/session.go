// Package session publishes the server-side browser session contract.
package session

import "time"

// BrowserSession is a server-side support record. Browser cookies carry only
// an opaque session identity and must not contain this complete record.
type BrowserSession struct {
	SessionID              string     `json:"session_id"`
	PrincipalID            string     `json:"principal_id"`
	AuthenticationMethodID string     `json:"authentication_method_id"`
	IssuedAt               time.Time  `json:"issued_at"`
	ExpiresAt              time.Time  `json:"expires_at"`
	RevokedAt              *time.Time `json:"revoked_at,omitempty"`
	SelectedScopeID        string     `json:"selected_scope_id,omitempty"`
}

// IsActiveAt reports whether a server-side session may still be used to
// rebuild a RequestContext. Scope selection remains subject to authorization.
func (session BrowserSession) IsActiveAt(now time.Time) bool {
	return session.RevokedAt == nil && now.Before(session.ExpiresAt)
}

// Package authentication defines the portable authentication.oac.dev/v1 driver contract.
package authentication

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/mail"
	"net/url"
	"path"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode"
)

const APIVersion = "authentication.oac.dev/v1"

const sha256DigestPrefix = "sha256:"

var exactSemanticVersionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$`)
var canonicalSHA256DigestPattern = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)

type ErrorCode string

const (
	ErrorInvalidCredentials ErrorCode = "invalid_credentials"
	ErrorInvalidState       ErrorCode = "invalid_state"
	ErrorFlowConsumed       ErrorCode = "flow_consumed"
	ErrorFlowInProgress     ErrorCode = "flow_in_progress"
	ErrorFlowExpired        ErrorCode = "flow_expired"
	ErrorStaleConfiguration ErrorCode = "stale_configuration"
)

type Error struct {
	Code    ErrorCode
	Message string
}

func (e *Error) Error() string {
	if e == nil {
		return "authentication error"
	}
	return e.Message
}

func CodeOf(err error) ErrorCode {
	var typed *Error
	if errors.As(err, &typed) && typed != nil {
		return typed.Code
	}
	return ""
}

// AuthenticationDriver receives Host-owned method and operation inputs and returns a Driver-owned identity.
type AuthenticationDriver interface {
	DescribeAuthenticationMethod(context.Context, DescribeAuthenticationMethodRequest) (DriverDeclaration, error)
	StartAuthentication(context.Context, StartAuthenticationRequest) (AuthenticationChallenge, error)
	CompleteAuthentication(context.Context, CompleteAuthenticationRequest) (AuthenticatedIdentity, error)
	ProbeAuthenticationMethod(context.Context, ProbeAuthenticationMethodRequest) (ProbeResult, error)
}

// DriverRelease identifies the immutable AuthenticationDriver release selected
// for a method. A version range or mutable channel is not a release identity.
type DriverRelease struct {
	ID             string
	Version        string
	ArtifactDigest string
}

// AuthenticationDriverIdentity is a descriptive alias for DriverRelease.
type AuthenticationDriverIdentity = DriverRelease

// DriverIdentity is a concise alias for DriverRelease.
type DriverIdentity = DriverRelease

func (r DriverRelease) Validate() error {
	if err := stableID("driver release ID", r.ID); err != nil {
		return err
	}
	if !exactSemanticVersionPattern.MatchString(r.Version) {
		return fmt.Errorf("driver release version must be an exact semantic version")
	}
	if !canonicalSHA256DigestPattern.MatchString(r.ArtifactDigest) {
		return fmt.Errorf("driver release artifact digest must be a canonical SHA-256 digest")
	}
	return nil
}

type MethodReference struct {
	ID, ConfigurationDigest string
	DriverRelease           DriverRelease
}

func (m MethodReference) Validate() error {
	if err := nonEmpty("method ID", m.ID, "configuration digest", m.ConfigurationDigest); err != nil {
		return err
	}
	return m.DriverRelease.Validate()
}

// AuthenticationMethod is Host-owned, including presentation, non-secret configuration and bindings.
type AuthenticationMethod struct {
	Reference           MethodReference
	Name, Description   string
	Configuration       map[string]string
	ConfigurationPolicy ConfigurationSafetyPolicy
	CredentialBindings  map[CredentialPurpose]CredentialBinding
}

func (m AuthenticationMethod) Validate() error {
	if err := m.Reference.Validate(); err != nil {
		return err
	}
	if err := nonEmpty("method name", m.Name, "method description", m.Description); err != nil {
		return err
	}
	if m.Configuration == nil || m.CredentialBindings == nil {
		return fmt.Errorf("method configuration and credential bindings must be non-nil")
	}
	if err := m.ConfigurationPolicy.Validate(); err != nil {
		return err
	}
	for purpose, binding := range m.CredentialBindings {
		if err := stableID("credential purpose", string(purpose)); err != nil {
			return err
		}
		if err := binding.Validate(); err != nil {
			return err
		}
	}
	return nil
}

type CredentialPurpose string
type CredentialBinding struct{ ID string }

// CredentialBindingPurpose makes the typed purpose boundary explicit to
// callers that prefer the domain name used by the Host contract.
type CredentialBindingPurpose = CredentialPurpose

func (b CredentialBinding) Validate() error { return stableID("credential binding ID", b.ID) }

type CredentialCapability interface {
	ReadCredential(context.Context) ([]byte, error)
}

type OperationContext struct {
	Method      AuthenticationMethod
	Now         time.Time
	Credentials map[CredentialPurpose]CredentialCapability
}

func cloneAuthenticationMethod(method AuthenticationMethod) AuthenticationMethod {
	copy := method
	copy.Configuration = cloneConfigurationValues(method.Configuration)
	copy.ConfigurationPolicy.AllowedNonSensitiveFields = cloneConfigurationPolicyValues(method.ConfigurationPolicy.AllowedNonSensitiveFields)
	copy.CredentialBindings = cloneCredentialBindingValues(method.CredentialBindings)
	return copy
}

func cloneOperationContext(operation OperationContext) OperationContext {
	copy := operation
	copy.Method = cloneAuthenticationMethod(operation.Method)
	copy.Credentials = cloneCredentialCapabilityValues(operation.Credentials)
	return copy
}

func cloneStartAuthenticationRequest(request StartAuthenticationRequest) StartAuthenticationRequest {
	copy := request
	copy.Operation = cloneOperationContext(request.Operation)
	return copy
}

func cloneCompleteAuthenticationRequest(request CompleteAuthenticationRequest) CompleteAuthenticationRequest {
	copy := request
	copy.Operation = cloneOperationContext(request.Operation)
	copy.Continuation = append([]byte(nil), request.Continuation...)
	copy.Response = cloneResponse(request.Response)
	return copy
}

func (c OperationContext) ValidateAt(declaration DriverDeclaration) error {
	if err := declaration.ValidateFor(c.Method); err != nil {
		return err
	}
	if c.Now.IsZero() || c.Credentials == nil {
		return fmt.Errorf("host time and credential capabilities must be non-nil")
	}
	for purpose := range c.Method.CredentialBindings {
		if _, declared := declaration.Credentials[purpose]; !declared {
			return fmt.Errorf("undeclared credential binding %q", purpose)
		}
	}
	for purpose := range c.Credentials {
		if _, declared := declaration.Credentials[purpose]; !declared {
			return fmt.Errorf("undeclared credential capability %q", purpose)
		}
	}
	for purpose, requirement := range declaration.Credentials {
		binding, hasBinding := c.Method.CredentialBindings[purpose]
		capability, hasCapability := c.Credentials[purpose]
		if hasBinding != hasCapability {
			return fmt.Errorf("credential %q binding and capability must be paired", purpose)
		}
		if requirement.Required && !hasBinding {
			return fmt.Errorf("required credential %q is not authorized", purpose)
		}
		if hasBinding {
			if err := binding.Validate(); err != nil {
				return err
			}
			if capabilityIsNil(capability) {
				return fmt.Errorf("credential capability %q is nil", purpose)
			}
		}
	}
	return nil
}

type ConfigurationValueType string

const (
	ConfigurationString   ConfigurationValueType = "string"
	ConfigurationHTTPSURL ConfigurationValueType = "https-url"
)

// ConfigurationSafetyPolicy is Host-owned approval for ordinary
// non-sensitive configuration. A Driver cannot make an unapproved field safe
// merely by classifying it as non-sensitive.
type ConfigurationSafetyPolicy struct {
	AllowedNonSensitiveFields map[string]ConfigurationValueType
}

func (p ConfigurationSafetyPolicy) Validate() error {
	for name, valueType := range p.AllowedNonSensitiveFields {
		if err := stableID("host-approved configuration key", name); err != nil {
			return err
		}
		if valueType != ConfigurationString && valueType != ConfigurationHTTPSURL {
			return fmt.Errorf("host-approved configuration type is invalid")
		}
	}
	return nil
}

// ConfigurationClassification identifies where a configuration value may be
// kept. Credential-bound values are represented only by a CredentialBinding
// purpose and never by AuthenticationMethod.Configuration.
type ConfigurationClassification string

const (
	ConfigurationNonSensitive      ConfigurationClassification = "non-sensitive"
	ConfigurationCredentialBinding ConfigurationClassification = "credential-binding"
)

// ConfigurationFieldClassification is a descriptive alias for callers that
// prefer the longer type name.
type ConfigurationFieldClassification = ConfigurationClassification

// ConfigurationField is a bounded Driver-owned schema for one Host configuration value.
type ConfigurationField struct {
	Label             string
	Type              ConfigurationValueType
	Required          bool
	MaxLength         int
	AllowedValues     []string
	Classification    ConfigurationClassification
	CredentialPurpose CredentialPurpose
	IdentityDefining  bool
}

func (f ConfigurationField) Validate() error {
	if err := nonEmpty("configuration label", f.Label); err != nil {
		return err
	}
	switch f.Classification {
	case ConfigurationNonSensitive:
		if f.CredentialPurpose != "" {
			return fmt.Errorf("non-sensitive configuration cannot name a credential purpose")
		}
		return f.validateNonSensitive()
	case ConfigurationCredentialBinding:
		if err := stableID("configuration credential purpose", string(f.CredentialPurpose)); err != nil {
			return err
		}
		if f.IdentityDefining {
			return fmt.Errorf("credential-bound configuration cannot define identity")
		}
		if f.Type != "" || f.MaxLength != 0 || len(f.AllowedValues) != 0 {
			return fmt.Errorf("credential-bound configuration cannot declare an ordinary value schema")
		}
		return nil
	default:
		return fmt.Errorf("configuration classification is required")
	}
}

func (f ConfigurationField) ValidateValue(value string) error {
	if f.Classification == ConfigurationCredentialBinding {
		return fmt.Errorf("credential-bound configuration must use its CredentialBinding purpose")
	}
	if f.Classification != ConfigurationNonSensitive {
		return fmt.Errorf("configuration classification is required")
	}
	if value == "" && !f.Required {
		return nil
	}
	if value == "" || len(value) > f.MaxLength {
		return fmt.Errorf("value is missing or too long")
	}
	if f.Type == ConfigurationHTTPSURL {
		if err := httpsURL(value); err != nil {
			return err
		}
	}
	if len(f.AllowedValues) > 0 {
		allowed := false
		for _, candidate := range f.AllowedValues {
			if value == candidate {
				allowed = true
				break
			}
		}
		if !allowed {
			return fmt.Errorf("value is not allowed")
		}
	}
	return nil
}

func (f ConfigurationField) validateNonSensitive() error {
	if f.IdentityDefining && !f.Required {
		return fmt.Errorf("identity-defining configuration must be required")
	}
	if f.Type != ConfigurationString && f.Type != ConfigurationHTTPSURL {
		return fmt.Errorf("configuration type is invalid")
	}
	if f.MaxLength < 1 || f.MaxLength > 4096 {
		return fmt.Errorf("configuration max length is out of bounds")
	}
	seen := map[string]bool{}
	for _, value := range f.AllowedValues {
		if value == "" || len(value) > f.MaxLength || seen[value] {
			return fmt.Errorf("configuration allowed values are invalid")
		}
		seen[value] = true
	}
	return nil
}

type CredentialRequirement struct {
	Label    string
	Required bool
}

func (r CredentialRequirement) Validate() error { return nonEmpty("credential label", r.Label) }

type AttributeKind string

const AttributeString AttributeKind = "string"

type AttributeRule struct{ Kind AttributeKind }

func (r AttributeRule) Validate() error {
	if r.Kind != AttributeString {
		return fmt.Errorf("attribute kind is invalid")
	}
	return nil
}

// IdentityStabilityProof is the Driver's public proof of the identity
// namespace represented by one method configuration. Configuration contains
// exactly the values of fields marked IdentityDefining by the declaration.
// The proof is tied to the exact Driver release selected by the Host.
type IdentityStabilityProof struct {
	DriverRelease       DriverRelease
	Configuration       map[string]string
	IssuerKeySemantics  string
	SubjectKeySemantics string
}

// IdentityStabilityDescriptor is retained as a descriptive compatibility
// alias for callers of the earlier SDK name.
type IdentityStabilityDescriptor = IdentityStabilityProof

func (p IdentityStabilityProof) Validate() error {
	if err := p.DriverRelease.Validate(); err != nil {
		return err
	}
	if p.Configuration == nil {
		return fmt.Errorf("identity proof configuration must be non-nil")
	}
	if err := nonEmpty("identity issuer-key semantics", p.IssuerKeySemantics, "identity subject-key semantics", p.SubjectKeySemantics); err != nil {
		return err
	}
	for name, value := range p.Configuration {
		if err := stableID("identity configuration key", name); err != nil {
			return err
		}
		if hasControl(value) {
			return fmt.Errorf("identity configuration value %q is invalid", name)
		}
	}
	return nil
}

// Digest returns a deterministic digest of the complete proof, including the
// exact Driver release and every identity-defining configuration value.
func (p IdentityStabilityProof) Digest() ([sha256.Size]byte, error) {
	if err := p.Validate(); err != nil {
		return [sha256.Size]byte{}, err
	}
	payload, err := json.Marshal(struct {
		DriverRelease       DriverRelease     `json:"driver_release"`
		Configuration       map[string]string `json:"configuration"`
		IssuerKeySemantics  string            `json:"issuer_key_semantics"`
		SubjectKeySemantics string            `json:"subject_key_semantics"`
	}{
		DriverRelease:       p.DriverRelease,
		Configuration:       p.Configuration,
		IssuerKeySemantics:  p.IssuerKeySemantics,
		SubjectKeySemantics: p.SubjectKeySemantics,
	})
	if err != nil {
		return [sha256.Size]byte{}, fmt.Errorf("marshal identity stability proof: %w", err)
	}
	return sha256.Sum256(payload), nil
}

// CanonicalDigest returns the proof digest in the same canonical form used by
// other public OAC SDK identities.
func (p IdentityStabilityProof) CanonicalDigest() (string, error) {
	digest, err := p.Digest()
	if err != nil {
		return "", err
	}
	return sha256DigestPrefix + hex.EncodeToString(digest[:]), nil
}

// DriverDeclaration owns only the Driver schema and one interaction presentation.
type DriverDeclaration struct {
	Configuration     map[string]ConfigurationField
	Credentials       map[CredentialPurpose]CredentialRequirement
	Presentation      PresentationInteraction
	Attributes        map[string]AttributeRule
	IdentityStability IdentityStabilityProof
}

func (d DriverDeclaration) Validate() error {
	if d.Configuration == nil || d.Credentials == nil || d.Attributes == nil {
		return fmt.Errorf("declaration maps must be non-nil")
	}
	if err := d.Presentation.Validate(); err != nil {
		return err
	}
	for name, field := range d.Configuration {
		if err := stableID("configuration key", name); err != nil {
			return err
		}
		if err := field.Validate(); err != nil {
			return err
		}
		if field.Classification == ConfigurationCredentialBinding {
			requirement, declared := d.Credentials[field.CredentialPurpose]
			if !declared {
				return fmt.Errorf("configuration %q references undeclared credential purpose %q", name, field.CredentialPurpose)
			}
			if field.Required != requirement.Required {
				return fmt.Errorf("configuration %q requiredness must match credential purpose %q", name, field.CredentialPurpose)
			}
		}
	}
	for purpose, requirement := range d.Credentials {
		if err := stableID("credential purpose", string(purpose)); err != nil {
			return err
		}
		if err := requirement.Validate(); err != nil {
			return err
		}
	}
	for name, rule := range d.Attributes {
		if err := stableID("attribute name", name); err != nil {
			return err
		}
		if name != strings.ToLower(name) {
			return fmt.Errorf("attribute name must be normalized")
		}
		if err := rule.Validate(); err != nil {
			return err
		}
	}
	return d.IdentityStability.Validate()
}

// ValidateFor checks a declaration against the exact method input supplied by
// the Host. It is the public fail-closed boundary for configuration and
// identity-proof drift.
func (d DriverDeclaration) ValidateFor(method AuthenticationMethod) error {
	if err := method.Validate(); err != nil {
		return err
	}
	if err := d.Validate(); err != nil {
		return err
	}
	if err := validateCredentialBindingSet(method.CredentialBindings, d.Credentials); err != nil {
		return err
	}
	if err := validateConfiguration(method.Configuration, d, method.ConfigurationPolicy); err != nil {
		return err
	}
	proof := d.IdentityStability
	if proof.DriverRelease != method.Reference.DriverRelease {
		return fmt.Errorf("identity proof Driver release does not match the method")
	}
	expected := make(map[string]string)
	for name, field := range d.Configuration {
		if field.IdentityDefining {
			expected[name] = method.Configuration[name]
		}
	}
	if len(proof.Configuration) != len(expected) {
		return fmt.Errorf("identity proof does not cover every identity-defining configuration value")
	}
	for name, value := range proof.Configuration {
		expectedValue, identityDefining := expected[name]
		if !identityDefining {
			return fmt.Errorf("identity proof contains non-identity configuration %q", name)
		}
		if value != expectedValue {
			return fmt.Errorf("identity proof configuration %q does not match the method", name)
		}
	}
	return nil
}

// VerifyIdentityStability is a concise public entry point for Hosts that
// validate a Driver declaration without running a full ceremony.
func VerifyIdentityStability(method AuthenticationMethod, declaration DriverDeclaration) error {
	return declaration.ValidateFor(method)
}

type DescribeAuthenticationMethodRequest struct{ Method AuthenticationMethod }
type AuthenticationMethodDescriptor struct {
	MethodID, Name, Description string
	Presentation                PresentationInteraction
}

func NewAuthenticationMethodDescriptor(method AuthenticationMethod, declaration DriverDeclaration) (AuthenticationMethodDescriptor, error) {
	if err := declaration.ValidateFor(method); err != nil {
		return AuthenticationMethodDescriptor{}, err
	}
	return AuthenticationMethodDescriptor{MethodID: method.Reference.ID, Name: method.Name, Description: method.Description, Presentation: declaration.Presentation}, nil
}

type InteractionKind string

const (
	InteractionCredentialsForm InteractionKind = "credentials-form"
	InteractionBrowserRedirect InteractionKind = "browser-redirect"
	InteractionNonBrowser      InteractionKind = "non-browser"
)

type CredentialFieldKind string

const (
	CredentialFieldText     CredentialFieldKind = "text"
	CredentialFieldPassword CredentialFieldKind = "password"
	CredentialFieldOTP      CredentialFieldKind = "otp"
)

type CredentialField struct {
	Name, Label  string
	Kind         CredentialFieldKind
	Required     bool
	Autocomplete string
}
type CredentialsFormDescriptor struct{ Fields []CredentialField }
type BrowserRedirectDescriptor struct{ CallbackParameter string }
type NonBrowserDescriptor struct{ UserCodeLabel string }

// PresentationInteraction is a strict exactly-one union selected by a Driver.
type PresentationInteraction struct {
	CredentialsForm *CredentialsFormDescriptor
	BrowserRedirect *BrowserRedirectDescriptor
	NonBrowser      *NonBrowserDescriptor
}

func (p PresentationInteraction) Validate() error {
	count := boolCount(p.CredentialsForm != nil, p.BrowserRedirect != nil, p.NonBrowser != nil)
	if count != 1 {
		return fmt.Errorf("presentation must contain exactly one interaction")
	}
	if p.CredentialsForm != nil {
		return p.CredentialsForm.Validate()
	}
	if p.BrowserRedirect != nil {
		return p.BrowserRedirect.Validate()
	}
	return p.NonBrowser.Validate()
}
func (p PresentationInteraction) Kind() (InteractionKind, error) {
	if err := p.Validate(); err != nil {
		return "", err
	}
	if p.CredentialsForm != nil {
		return InteractionCredentialsForm, nil
	}
	if p.BrowserRedirect != nil {
		return InteractionBrowserRedirect, nil
	}
	return InteractionNonBrowser, nil
}
func (d CredentialsFormDescriptor) Validate() error {
	if len(d.Fields) == 0 {
		return fmt.Errorf("credentials form needs fields")
	}
	seen := map[string]bool{}
	for _, field := range d.Fields {
		if err := stableID("credential field name", field.Name); err != nil {
			return err
		}
		if err := nonEmpty("credential field label", field.Label); err != nil {
			return err
		}
		if field.Kind != CredentialFieldText && field.Kind != CredentialFieldPassword && field.Kind != CredentialFieldOTP {
			return fmt.Errorf("credential field kind is invalid")
		}
		if field.Autocomplete != "" && field.Autocomplete != "username" && field.Autocomplete != "current-password" && field.Autocomplete != "one-time-code" {
			return fmt.Errorf("credential autocomplete is invalid")
		}
		if seen[field.Name] {
			return fmt.Errorf("credential field is duplicated")
		}
		seen[field.Name] = true
	}
	return nil
}
func (d BrowserRedirectDescriptor) Validate() error {
	return stableID("browser callback parameter", d.CallbackParameter)
}
func (d NonBrowserDescriptor) Validate() error {
	return nonEmpty("non-browser user code label", d.UserCodeLabel)
}

type AuthenticationPurpose string

const (
	AuthenticationPurposeLogin AuthenticationPurpose = "login"
	AuthenticationPurposeLink  AuthenticationPurpose = "link"
	AuthenticationPurposeTest  AuthenticationPurpose = "test"
)

func (p AuthenticationPurpose) Validate() error {
	if p != AuthenticationPurposeLogin && p != AuthenticationPurposeLink && p != AuthenticationPurposeTest {
		return fmt.Errorf("authentication purpose is invalid")
	}
	return nil
}

type StartAuthenticationRequest struct {
	Operation          OperationContext
	FlowID, StateToken string
	Purpose            AuthenticationPurpose
}

func (r StartAuthenticationRequest) Validate(declaration DriverDeclaration) error {
	if err := r.Operation.ValidateAt(declaration); err != nil {
		return err
	}
	if err := nonEmpty("flow ID", r.FlowID, "state token", r.StateToken); err != nil {
		return err
	}
	return r.Purpose.Validate()
}

// ProtectedStateEnvelope is Host-owned persisted ciphertext and key metadata.
// It is deliberately absent from every AuthenticationDriver request and response.
type ProtectedStateEnvelope struct {
	Version, KeyID string
	Ciphertext     []byte
}

// FlowStateContext is immutable Host-owned associated data for protected continuation state.
// A HostStateProtector must authenticate this context with the ciphertext it seals.
type FlowStateContext struct {
	DriverRelease                                    DriverRelease
	APIDomain, FlowID, MethodID, ConfigurationDigest string
	Purpose                                          AuthenticationPurpose
	ExpiresAt                                        time.Time
	StateTokenDigest                                 [sha256.Size]byte
}

func (c FlowStateContext) Validate() error {
	if err := c.DriverRelease.Validate(); err != nil {
		return err
	}
	if c.APIDomain != APIVersion {
		return fmt.Errorf("flow state API domain is invalid")
	}
	if err := nonEmpty("flow state flow ID", c.FlowID, "flow state method ID", c.MethodID, "flow state configuration digest", c.ConfigurationDigest); err != nil {
		return err
	}
	if err := c.Purpose.Validate(); err != nil {
		return err
	}
	if c.ExpiresAt.IsZero() {
		return fmt.Errorf("flow state expiry is invalid")
	}
	return nil
}

func (e ProtectedStateEnvelope) Validate() error {
	if err := nonEmpty("protected state version", e.Version, "protected state key ID", e.KeyID); err != nil {
		return err
	}
	if len(e.Ciphertext) == 0 {
		return fmt.Errorf("protected state ciphertext is required")
	}
	return nil
}
func (e ProtectedStateEnvelope) Digest() [sha256.Size]byte {
	payload := make([]byte, 0, len(e.Version)+len(e.KeyID)+len(e.Ciphertext)+2)
	payload = append(payload, e.Version...)
	payload = append(payload, '\x00')
	payload = append(payload, e.KeyID...)
	payload = append(payload, '\x00')
	payload = append(payload, e.Ciphertext...)
	return sha256.Sum256(payload)
}

// HostStateProtector is the Host trust boundary around Driver continuation state.
// Implementations must authenticate ciphertext as well as encrypt it.
type HostStateProtector interface {
	Seal(context.Context, FlowStateContext, []byte) (ProtectedStateEnvelope, error)
	Open(context.Context, FlowStateContext, ProtectedStateEnvelope) ([]byte, error)
}

type CredentialsChallenge struct{ Message string }
type BrowserRedirectChallenge struct{ AuthorizationURL string }
type NonBrowserChallenge struct{ Challenge string }
type AuthenticationChallenge struct {
	FlowID, ConfigurationDigest, StateToken string
	Continuation                            []byte
	CredentialsForm                         *CredentialsChallenge
	BrowserRedirect                         *BrowserRedirectChallenge
	NonBrowser                              *NonBrowserChallenge
}

func (c AuthenticationChallenge) Validate(expected InteractionKind) error {
	if err := nonEmpty("challenge flow ID", c.FlowID, "challenge configuration digest", c.ConfigurationDigest, "challenge state token", c.StateToken); err != nil {
		return err
	}
	if len(c.Continuation) == 0 {
		return fmt.Errorf("challenge continuation is required")
	}
	count := boolCount(c.CredentialsForm != nil, c.BrowserRedirect != nil, c.NonBrowser != nil)
	if count != 1 {
		return fmt.Errorf("challenge must contain exactly one interaction")
	}
	switch expected {
	case InteractionCredentialsForm:
		if c.CredentialsForm == nil {
			return fmt.Errorf("challenge interaction mismatch")
		}
		return nonEmpty("credentials challenge message", c.CredentialsForm.Message)
	case InteractionBrowserRedirect:
		if c.BrowserRedirect == nil {
			return fmt.Errorf("challenge interaction mismatch")
		}
		return httpsURL(c.BrowserRedirect.AuthorizationURL)
	case InteractionNonBrowser:
		if c.NonBrowser == nil {
			return fmt.Errorf("challenge interaction mismatch")
		}
		return nonEmpty("non-browser challenge", c.NonBrowser.Challenge)
	default:
		return fmt.Errorf("challenge interaction is invalid")
	}
}

type LocalReturnPath string

func ParseLocalReturnPath(raw string) (LocalReturnPath, error) {
	if raw == "" || !strings.HasPrefix(raw, "/") || strings.HasPrefix(raw, "//") || strings.Contains(raw, "\\") || hasControl(raw) || strings.ContainsAny(raw, "?#") {
		return "", fmt.Errorf("return path must be a platform-local path")
	}
	decoded, err := url.PathUnescape(raw)
	if err != nil || strings.HasPrefix(decoded, "//") || strings.Contains(decoded, "\\") || hasControl(decoded) || strings.ContainsAny(decoded, "?#") || strings.Contains(decoded, "%") {
		return "", fmt.Errorf("return path encoding is unsafe")
	}
	decodedAgain, err := url.PathUnescape(decoded)
	if err != nil || decodedAgain != decoded {
		return "", fmt.Errorf("return path decoding is not idempotent")
	}
	canonical := path.Clean(decoded)
	if !strings.HasPrefix(canonical, "/") || strings.HasPrefix(canonical, "//") || strings.ContainsAny(canonical, "?#%") {
		return "", fmt.Errorf("return path is not local")
	}
	return LocalReturnPath(canonical), nil
}

// AuthenticationFlow is the Host-persisted recoverable ceremony record.
type AuthenticationFlow struct {
	ID, MethodID, ConfigurationDigest      string
	DriverRelease                          DriverRelease
	StateTokenDigest, ProtectedStateDigest [sha256.Size]byte
	ProtectedState                         ProtectedStateEnvelope
	ExpiresAt                              time.Time
	ConsumedAt                             *time.Time
	Purpose                                AuthenticationPurpose
	ReturnPath                             LocalReturnPath
}

func NewAuthenticationFlow(ctx context.Context, start StartAuthenticationRequest, declaration DriverDeclaration, challenge AuthenticationChallenge, protector HostStateProtector, expiresAt time.Time, returnPath string) (AuthenticationFlow, error) {
	if err := start.Validate(declaration); err != nil {
		return AuthenticationFlow{}, err
	}
	kind, err := declaration.Presentation.Kind()
	if err != nil {
		return AuthenticationFlow{}, err
	}
	if err := challenge.Validate(kind); err != nil {
		return AuthenticationFlow{}, err
	}
	if challenge.FlowID != start.FlowID || challenge.StateToken != start.StateToken {
		return AuthenticationFlow{}, &Error{Code: ErrorInvalidState, Message: "challenge correlation failed"}
	}
	if challenge.ConfigurationDigest != start.Operation.Method.Reference.ConfigurationDigest {
		return AuthenticationFlow{}, &Error{Code: ErrorStaleConfiguration, Message: "challenge configuration changed"}
	}
	if expiresAt.IsZero() {
		return AuthenticationFlow{}, fmt.Errorf("flow expiry is invalid")
	}
	local, err := ParseLocalReturnPath(returnPath)
	if err != nil {
		return AuthenticationFlow{}, err
	}
	stateTokenDigest := sha256.Sum256([]byte(challenge.StateToken))
	flow := AuthenticationFlow{ID: challenge.FlowID, MethodID: start.Operation.Method.Reference.ID, ConfigurationDigest: start.Operation.Method.Reference.ConfigurationDigest, DriverRelease: start.Operation.Method.Reference.DriverRelease, StateTokenDigest: stateTokenDigest, ExpiresAt: expiresAt, Purpose: start.Purpose, ReturnPath: local}
	if interfaceIsNil(protector) {
		return AuthenticationFlow{}, fmt.Errorf("host state protector is required")
	}
	protectedState, err := protector.Seal(ctx, flow.StateContext(), append([]byte(nil), challenge.Continuation...))
	if err != nil {
		return AuthenticationFlow{}, fmt.Errorf("seal driver continuation: %w", err)
	}
	if err := protectedState.Validate(); err != nil {
		return AuthenticationFlow{}, err
	}
	flow.ProtectedState = protectedState
	flow.ProtectedStateDigest = protectedState.Digest()
	return flow, nil
}

func (f AuthenticationFlow) StateContext() FlowStateContext {
	return FlowStateContext{DriverRelease: f.DriverRelease, APIDomain: APIVersion, FlowID: f.ID, MethodID: f.MethodID, ConfigurationDigest: f.ConfigurationDigest, Purpose: f.Purpose, ExpiresAt: f.ExpiresAt, StateTokenDigest: f.StateTokenDigest}
}

func (f AuthenticationFlow) ValidateAt(method MethodReference, now time.Time) error {
	if err := method.Validate(); err != nil {
		return err
	}
	if err := nonEmpty("flow ID", f.ID, "flow method ID", f.MethodID, "flow configuration digest", f.ConfigurationDigest); err != nil {
		return err
	}
	if f.MethodID != method.ID {
		return &Error{Code: ErrorInvalidState, Message: "flow method changed"}
	}
	if f.ConfigurationDigest != method.ConfigurationDigest {
		return &Error{Code: ErrorStaleConfiguration, Message: "flow configuration changed"}
	}
	if f.DriverRelease != method.DriverRelease {
		return &Error{Code: ErrorStaleConfiguration, Message: "flow driver release changed"}
	}
	if f.ConsumedAt != nil {
		return &Error{Code: ErrorFlowConsumed, Message: "flow is consumed"}
	}
	if now.IsZero() || f.ExpiresAt.IsZero() || !f.ExpiresAt.After(now) {
		return &Error{Code: ErrorFlowExpired, Message: "flow is expired"}
	}
	if err := f.Purpose.Validate(); err != nil {
		return err
	}
	if err := f.StateContext().Validate(); err != nil {
		return err
	}
	canonicalReturnPath, err := ParseLocalReturnPath(string(f.ReturnPath))
	if err != nil {
		return err
	}
	if canonicalReturnPath != f.ReturnPath {
		return fmt.Errorf("flow return path is not canonical")
	}
	if err := f.ProtectedState.Validate(); err != nil {
		return err
	}
	if f.ProtectedState.Digest() != f.ProtectedStateDigest {
		return &Error{Code: ErrorInvalidState, Message: "protected state recovery failed"}
	}
	return nil
}
func RecoverAuthenticationFlow(flow AuthenticationFlow, method MethodReference, now time.Time) (AuthenticationFlow, error) {
	if err := flow.ValidateAt(method, now); err != nil {
		return AuthenticationFlow{}, err
	}
	return flow, nil
}

// OpenAuthenticationFlow validates Host-persisted state before returning plaintext only to the Driver call path.
func OpenAuthenticationFlow(ctx context.Context, flow AuthenticationFlow, method MethodReference, now time.Time, protector HostStateProtector) ([]byte, error) {
	if err := flow.ValidateAt(method, now); err != nil {
		return nil, err
	}
	if interfaceIsNil(protector) {
		return nil, fmt.Errorf("host state protector is required")
	}
	continuation, err := protector.Open(ctx, flow.StateContext(), flow.ProtectedState)
	if err != nil {
		if CodeOf(err) == ErrorInvalidState {
			return nil, err
		}
		return nil, fmt.Errorf("open protected state: %w", err)
	}
	if len(continuation) == 0 {
		return nil, &Error{Code: ErrorInvalidState, Message: "protected state continuation is empty"}
	}
	return append([]byte(nil), continuation...), nil
}

// AuthenticationFlowStore atomically claims one flow at a time, then either finalizes
// its consumption or releases it for a retry.
type AuthenticationFlowStore interface {
	Claim(context.Context, string) (AuthenticationFlow, error)
	Finalize(context.Context, string, time.Time) error
	Release(context.Context, string) error
}

// InMemoryAuthenticationFlowStore is a concurrency-safe reference Host store for tests and conformance.
type InMemoryAuthenticationFlowStore struct {
	mu     sync.Mutex
	flows  map[string]AuthenticationFlow
	claims map[string]bool
}

func NewInMemoryAuthenticationFlowStore(flows ...AuthenticationFlow) *InMemoryAuthenticationFlowStore {
	store := &InMemoryAuthenticationFlowStore{flows: make(map[string]AuthenticationFlow, len(flows)), claims: make(map[string]bool, len(flows))}
	for _, flow := range flows {
		store.flows[flow.ID] = cloneAuthenticationFlow(flow)
	}
	return store
}

func (s *InMemoryAuthenticationFlowStore) Put(flow AuthenticationFlow) error {
	if s == nil || s.flows == nil || s.claims == nil {
		return fmt.Errorf("flow store is not initialized")
	}
	if err := nonEmpty("flow ID", flow.ID); err != nil {
		return err
	}
	if flow.ConsumedAt != nil {
		return fmt.Errorf("consumed flow cannot be inserted")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, found := s.flows[flow.ID]; found {
		return fmt.Errorf("flow already exists")
	}
	s.flows[flow.ID] = cloneAuthenticationFlow(flow)
	return nil
}

func (s *InMemoryAuthenticationFlowStore) Claim(_ context.Context, flowID string) (AuthenticationFlow, error) {
	if s == nil || s.flows == nil || s.claims == nil {
		return AuthenticationFlow{}, fmt.Errorf("flow store is not initialized")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	flow, found := s.flows[flowID]
	if !found {
		return AuthenticationFlow{}, &Error{Code: ErrorInvalidState, Message: "flow does not exist"}
	}
	if flow.ConsumedAt != nil {
		return AuthenticationFlow{}, &Error{Code: ErrorFlowConsumed, Message: "flow is consumed"}
	}
	if s.claims[flowID] {
		return AuthenticationFlow{}, &Error{Code: ErrorFlowInProgress, Message: "flow is in progress"}
	}
	s.claims[flowID] = true
	return cloneAuthenticationFlow(flow), nil
}

func (s *InMemoryAuthenticationFlowStore) Finalize(_ context.Context, flowID string, consumedAt time.Time) error {
	if s == nil || s.flows == nil || s.claims == nil || consumedAt.IsZero() {
		return fmt.Errorf("flow store and host time are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	flow, found := s.flows[flowID]
	if !found || !s.claims[flowID] {
		return &Error{Code: ErrorInvalidState, Message: "flow is not claimed"}
	}
	if flow.ConsumedAt != nil {
		return &Error{Code: ErrorFlowConsumed, Message: "flow is consumed"}
	}
	flow.ConsumedAt = &consumedAt
	s.flows[flowID] = flow
	delete(s.claims, flowID)
	return nil
}

func (s *InMemoryAuthenticationFlowStore) Release(_ context.Context, flowID string) error {
	if s == nil || s.flows == nil || s.claims == nil {
		return fmt.Errorf("flow store is not initialized")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, found := s.flows[flowID]; !found || !s.claims[flowID] {
		return &Error{Code: ErrorInvalidState, Message: "flow is not claimed"}
	}
	delete(s.claims, flowID)
	return nil
}

func (s *InMemoryAuthenticationFlowStore) Flow(flowID string) (AuthenticationFlow, bool) {
	if s == nil || s.flows == nil || s.claims == nil {
		return AuthenticationFlow{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	flow, found := s.flows[flowID]
	return cloneAuthenticationFlow(flow), found
}

func cloneAuthenticationFlow(flow AuthenticationFlow) AuthenticationFlow {
	copy := flow
	copy.ProtectedState.Ciphertext = append([]byte(nil), flow.ProtectedState.Ciphertext...)
	if flow.ConsumedAt != nil {
		consumedAt := *flow.ConsumedAt
		copy.ConsumedAt = &consumedAt
	}
	return copy
}

type CredentialsCompletion struct{ Values map[string]string }
type BrowserCallback struct{ CallbackURL string }
type NonBrowserCompletion struct{ Response string }

// AuthenticationResponse is a strict exactly-one completion union.
type AuthenticationResponse struct {
	Credentials     *CredentialsCompletion
	BrowserRedirect *BrowserCallback
	NonBrowser      *NonBrowserCompletion
}

func (r AuthenticationResponse) Validate(presentation PresentationInteraction) error {
	kind, err := presentation.Kind()
	if err != nil {
		return err
	}
	count := boolCount(r.Credentials != nil, r.BrowserRedirect != nil, r.NonBrowser != nil)
	if count != 1 {
		return fmt.Errorf("completion must contain exactly one interaction")
	}
	switch kind {
	case InteractionCredentialsForm:
		if r.Credentials == nil || r.Credentials.Values == nil {
			return fmt.Errorf("credentials completion mismatch")
		}
		fields := map[string]CredentialField{}
		for _, field := range presentation.CredentialsForm.Fields {
			fields[field.Name] = field
		}
		for name := range r.Credentials.Values {
			if _, declared := fields[name]; !declared {
				return fmt.Errorf("undeclared credential field %q", name)
			}
		}
		for name, field := range fields {
			if field.Required && strings.TrimSpace(r.Credentials.Values[name]) == "" {
				return fmt.Errorf("credential field %q is required", name)
			}
		}
		return nil
	case InteractionBrowserRedirect:
		if r.BrowserRedirect == nil {
			return fmt.Errorf("browser completion mismatch")
		}
		return httpsURL(r.BrowserRedirect.CallbackURL)
	case InteractionNonBrowser:
		if r.NonBrowser == nil {
			return fmt.Errorf("non-browser completion mismatch")
		}
		return nonEmpty("non-browser response", r.NonBrowser.Response)
	default:
		return fmt.Errorf("completion interaction is invalid")
	}
}

type CompleteAuthenticationRequest struct {
	Operation    OperationContext
	Continuation []byte
	Response     AuthenticationResponse
}

func (r CompleteAuthenticationRequest) Validate(declaration DriverDeclaration) error {
	if err := r.Operation.ValidateAt(declaration); err != nil {
		return err
	}
	if len(r.Continuation) == 0 {
		return fmt.Errorf("driver continuation is required")
	}
	return r.Response.Validate(declaration.Presentation)
}

// CompletedAuthentication carries Host-trusted flow metadata with the finalized subject.
type CompletedAuthentication struct {
	FlowID     string
	Purpose    AuthenticationPurpose
	ReturnPath LocalReturnPath
	Subject    AuthenticatedSubject
}

// CompleteAuthenticationFlow is the Host-owned claim/finalize/release boundary. It finalizes
// consumption only after success or a terminal flow violation, releasing retryable failures.
func CompleteAuthenticationFlow(ctx context.Context, driver AuthenticationDriver, declaration DriverDeclaration, operation OperationContext, store AuthenticationFlowStore, protector HostStateProtector, flowID, stateToken string, response AuthenticationResponse) (CompletedAuthentication, error) {
	if interfaceIsNil(driver) || interfaceIsNil(store) || interfaceIsNil(protector) {
		return CompletedAuthentication{}, fmt.Errorf("driver, flow store, and host state protector are required")
	}
	if err := operation.ValidateAt(declaration); err != nil {
		return CompletedAuthentication{}, err
	}
	flow, err := store.Claim(ctx, flowID)
	if err != nil {
		return CompletedAuthentication{}, err
	}
	release := func(result error) (CompletedAuthentication, error) {
		if releaseErr := store.Release(ctx, flowID); releaseErr != nil {
			return CompletedAuthentication{}, fmt.Errorf("release authentication flow claim: %w", releaseErr)
		}
		return CompletedAuthentication{}, result
	}
	finalize := func(result error) (CompletedAuthentication, error) {
		if finalizeErr := store.Finalize(ctx, flowID, operation.Now); finalizeErr != nil {
			return CompletedAuthentication{}, fmt.Errorf("finalize authentication flow consumption: %w", finalizeErr)
		}
		return CompletedAuthentication{}, result
	}
	if flow.ID != flowID {
		return release(&Error{Code: ErrorInvalidState, Message: "claimed flow does not match request"})
	}
	if err := flow.ValidateAt(operation.Method.Reference, operation.Now); err != nil {
		if isTerminalFlowError(err) {
			return finalize(err)
		}
		return release(err)
	}
	if sha256.Sum256([]byte(stateToken)) != flow.StateTokenDigest {
		return finalize(&Error{Code: ErrorInvalidState, Message: "state does not match flow"})
	}
	continuation, err := OpenAuthenticationFlow(ctx, flow, operation.Method.Reference, operation.Now, protector)
	if err != nil {
		if CodeOf(err) == ErrorInvalidState {
			return finalize(err)
		}
		return release(err)
	}
	request := CompleteAuthenticationRequest{Operation: operation, Continuation: continuation, Response: response}
	if err := request.Validate(declaration); err != nil {
		return release(err)
	}
	identity, err := driver.CompleteAuthentication(ctx, cloneCompleteAuthenticationRequest(request))
	if err != nil {
		if isTerminalFlowError(err) {
			return finalize(err)
		}
		return release(err)
	}
	subject, err := FinalizeAuthenticatedSubject(identity, operation, declaration)
	if err != nil {
		return finalize(err)
	}
	if err := store.Finalize(ctx, flowID, operation.Now); err != nil {
		return CompletedAuthentication{}, fmt.Errorf("finalize authentication flow consumption: %w", err)
	}
	return CompletedAuthentication{FlowID: flow.ID, Purpose: flow.Purpose, ReturnPath: flow.ReturnPath, Subject: subject}, nil
}

func isTerminalFlowError(err error) bool {
	switch CodeOf(err) {
	case ErrorInvalidState, ErrorFlowExpired, ErrorStaleConfiguration:
		return true
	default:
		return false
	}
}

type ProbeAuthenticationMethodRequest struct{ Operation OperationContext }
type ProbeStatus string

const (
	ProbeStatusHealthy   ProbeStatus = "healthy"
	ProbeStatusUnhealthy ProbeStatus = "unhealthy"
)

type Diagnostic struct{ Code, Message string }
type ProbeResult struct {
	Status      ProbeStatus
	Diagnostics []Diagnostic
}

func (r ProbeResult) Validate() error {
	if r.Status != ProbeStatusHealthy && r.Status != ProbeStatusUnhealthy {
		return fmt.Errorf("probe status is invalid")
	}
	for _, diagnostic := range r.Diagnostics {
		if err := nonEmpty("diagnostic code", diagnostic.Code, "diagnostic message", diagnostic.Message); err != nil {
			return err
		}
	}
	return nil
}

type AuthenticationStrength string

const (
	AuthenticationStrengthPassword    AuthenticationStrength = "password"
	AuthenticationStrengthMultiFactor AuthenticationStrength = "multi-factor"
	AuthenticationStrengthPasskey     AuthenticationStrength = "passkey"
)

type IdentityTuple struct{ AuthenticationMethodID, IssuerKey, SubjectKey string }

// AuthenticatedIdentity is Driver-owned and deliberately contains no timestamp.
type AuthenticatedIdentity struct {
	AuthenticationMethodID, IssuerKey, SubjectKey string
	AuthenticationStrength                        AuthenticationStrength
	DisplayName                                   DisplayName
	VerifiedEmail                                 VerifiedEmail
	Attributes                                    map[string]string
}

type DisplayName string

func (d DisplayName) Validate() error {
	if d != "" && (strings.TrimSpace(string(d)) == "" || hasControl(string(d))) {
		return fmt.Errorf("display name is invalid")
	}
	return nil
}

type VerifiedEmail string

func (e VerifiedEmail) Validate() error {
	if e == "" {
		return nil
	}
	parsed, err := mail.ParseAddress(string(e))
	if err != nil || parsed.Address != string(e) || hasControl(string(e)) {
		return fmt.Errorf("verified email is invalid")
	}
	return nil
}

func (i AuthenticatedIdentity) IdentityKey() IdentityTuple {
	return IdentityTuple{i.AuthenticationMethodID, i.IssuerKey, i.SubjectKey}
}
func (i AuthenticatedIdentity) ValidateFor(declaration DriverDeclaration) error {
	if err := nonEmpty("authentication method ID", i.AuthenticationMethodID, "issuer key", i.IssuerKey, "subject key", i.SubjectKey); err != nil {
		return err
	}
	if i.AuthenticationStrength != AuthenticationStrengthPassword && i.AuthenticationStrength != AuthenticationStrengthMultiFactor && i.AuthenticationStrength != AuthenticationStrengthPasskey {
		return fmt.Errorf("authentication strength is invalid")
	}
	if err := i.DisplayName.Validate(); err != nil {
		return err
	}
	if err := i.VerifiedEmail.Validate(); err != nil {
		return err
	}
	for name, value := range i.Attributes {
		if name == "email" {
			return fmt.Errorf("verified email must use the typed field")
		}
		if _, declared := declaration.Attributes[name]; !declared {
			return fmt.Errorf("undeclared subject attribute %q", name)
		}
		if hasControl(value) {
			return fmt.Errorf("subject attribute %q is invalid", name)
		}
	}
	return nil
}

// AuthenticatedSubject is the Host-published identity with trusted Host time.
type AuthenticatedSubject struct {
	AuthenticatedIdentity
	AuthenticatedAt time.Time
}

func (s AuthenticatedSubject) IdentityKey() IdentityTuple {
	return s.AuthenticatedIdentity.IdentityKey()
}
func (s AuthenticatedSubject) Validate() error {
	if err := s.AuthenticatedIdentity.ValidateFor(DriverDeclaration{Attributes: attributeRules(s.Attributes)}); err != nil {
		return err
	}
	if s.AuthenticatedAt.IsZero() {
		return fmt.Errorf("authenticated time is required")
	}
	return nil
}
func FinalizeAuthenticatedSubject(identity AuthenticatedIdentity, operation OperationContext, declaration DriverDeclaration) (AuthenticatedSubject, error) {
	if err := operation.ValidateAt(declaration); err != nil {
		return AuthenticatedSubject{}, err
	}
	if err := identity.ValidateFor(declaration); err != nil {
		return AuthenticatedSubject{}, err
	}
	if identity.AuthenticationMethodID != operation.Method.Reference.ID {
		return AuthenticatedSubject{}, fmt.Errorf("subject method mismatch")
	}
	subject := AuthenticatedSubject{AuthenticatedIdentity: identity, AuthenticatedAt: operation.Now}
	return subject, nil
}

func attributeRules(attributes map[string]string) map[string]AttributeRule {
	rules := make(map[string]AttributeRule, len(attributes))
	for name := range attributes {
		rules[name] = AttributeRule{Kind: AttributeString}
	}
	return rules
}
func boolCount(values ...bool) int {
	count := 0
	for _, value := range values {
		if value {
			count++
		}
	}
	return count
}
func nonEmpty(parts ...string) error {
	for index := 0; index < len(parts); index += 2 {
		if strings.TrimSpace(parts[index+1]) == "" || hasControl(parts[index+1]) {
			return fmt.Errorf("%s is required", parts[index])
		}
	}
	return nil
}
func stableID(label, value string) error {
	if err := nonEmpty(label, value); err != nil {
		return err
	}
	for _, runeValue := range value {
		if !(runeValue >= 'a' && runeValue <= 'z' || runeValue >= 'A' && runeValue <= 'Z' || runeValue >= '0' && runeValue <= '9' || runeValue == '-' || runeValue == '_') {
			return fmt.Errorf("%s must be a stable identifier", label)
		}
	}
	return nil
}

func cloneConfigurationValues(values map[string]string) map[string]string {
	if values == nil {
		return nil
	}
	copy := make(map[string]string, len(values))
	for key, value := range values {
		copy[key] = value
	}
	return copy
}

func cloneConfigurationPolicyValues(values map[string]ConfigurationValueType) map[string]ConfigurationValueType {
	if values == nil {
		return nil
	}
	copy := make(map[string]ConfigurationValueType, len(values))
	for key, value := range values {
		copy[key] = value
	}
	return copy
}

func cloneCredentialBindingValues(values map[CredentialPurpose]CredentialBinding) map[CredentialPurpose]CredentialBinding {
	if values == nil {
		return nil
	}
	copy := make(map[CredentialPurpose]CredentialBinding, len(values))
	for key, value := range values {
		copy[key] = value
	}
	return copy
}

func cloneCredentialCapabilityValues(values map[CredentialPurpose]CredentialCapability) map[CredentialPurpose]CredentialCapability {
	if values == nil {
		return nil
	}
	copy := make(map[CredentialPurpose]CredentialCapability, len(values))
	for key, value := range values {
		copy[key] = value
	}
	return copy
}

func validateConfiguration(configuration map[string]string, declaration DriverDeclaration, policy ConfigurationSafetyPolicy) error {
	for key, field := range declaration.Configuration {
		if field.Classification != ConfigurationNonSensitive {
			continue
		}
		approvedType, approved := policy.AllowedNonSensitiveFields[key]
		if !approved {
			return fmt.Errorf("configuration %q is not host-approved as non-sensitive", key)
		}
		if approvedType != field.Type {
			return fmt.Errorf("configuration %q type is not host-approved", key)
		}
	}
	for key, value := range configuration {
		field, declared := declaration.Configuration[key]
		if !declared {
			return fmt.Errorf("undeclared configuration key %q", key)
		}
		if field.Classification == ConfigurationCredentialBinding {
			return fmt.Errorf("configuration %q must use CredentialBinding purpose %q", key, field.CredentialPurpose)
		}
		if err := field.ValidateValue(value); err != nil {
			return fmt.Errorf("configuration %q: %w", key, err)
		}
	}
	for key, field := range declaration.Configuration {
		if field.Classification == ConfigurationCredentialBinding {
			if _, present := configuration[key]; present {
				return fmt.Errorf("credential-bound configuration %q cannot be supplied as an ordinary value", key)
			}
			continue
		}
		if err := field.ValidateValue(configuration[key]); err != nil {
			return fmt.Errorf("configuration %q: %w", key, err)
		}
	}
	return nil
}

func validateCredentialBindingSet(bindings map[CredentialPurpose]CredentialBinding, requirements map[CredentialPurpose]CredentialRequirement) error {
	for purpose := range bindings {
		if _, declared := requirements[purpose]; !declared {
			return fmt.Errorf("undeclared credential binding %q", purpose)
		}
	}
	for purpose, requirement := range requirements {
		if requirement.Required {
			if _, bound := bindings[purpose]; !bound {
				return fmt.Errorf("required credential %q is not bound", purpose)
			}
		}
	}
	return nil
}

func hasControl(value string) bool { return strings.IndexFunc(value, unicode.IsControl) >= 0 }

func capabilityIsNil(capability CredentialCapability) bool {
	return interfaceIsNil(capability)
}

func interfaceIsNil(value any) bool {
	if value == nil {
		return true
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Ptr, reflect.Slice:
		return reflected.IsNil()
	default:
		return false
	}
}
func httpsURL(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || hasControl(raw) {
		return fmt.Errorf("URL must be a safe HTTPS URL")
	}
	return nil
}

// Package audit defines immutable audit-event, export, and query contracts.
package audit

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"strings"
	"time"

	auditstore "github.com/xiaohei-info/open-agent-cluster/contracts/governance/audit/internal/auditstore"
	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
)

const AuditEventSortOrder = "occurred_at DESC, audit_event_id DESC"

type Category string

const (
	CategoryAuthentication Category = "authentication"
	CategoryAuthorization  Category = "authorization"
	CategoryAdmission      Category = "admission"
	CategoryDecision       Category = "decision"
	CategoryCredential     Category = "credential"
	CategoryStateWrite     Category = "state-write"
)

type Outcome string

const (
	OutcomeAllowed   Outcome = "allowed"
	OutcomeDenied    Outcome = "denied"
	OutcomeSucceeded Outcome = "succeeded"
	OutcomeFailed    Outcome = "failed"
)

type ControlKind string

const (
	ControlAuthorization ControlKind = "authorization"
	ControlAdmission     ControlKind = "admission"
	ControlProvider      ControlKind = "provider"
)

const (
	ControlResultAllowed   = "allowed"
	ControlResultDenied    = "denied"
	ControlResultSucceeded = "succeeded"
	ControlResultFailed    = "failed"
)

type AuthorizationControlResult string

const (
	AuthorizationControlAllow AuthorizationControlResult = "allow"
	AuthorizationControlDeny  AuthorizationControlResult = "deny"
)

type RedactionReason string

const (
	RedactionSecret        RedactionReason = "secret"
	RedactionNotAuthorized RedactionReason = "not_authorized"
	RedactionRetention     RedactionReason = "retention"
)

type SubjectKey struct {
	Kind   string `json:"kind"`
	ID     string `json:"id"`
	Digest string `json:"digest"`
}

type EvaluatedControl struct {
	Kind          ControlKind                     `json:"kind"`
	ControlID     string                          `json:"control_id"`
	Revision      string                          `json:"revision"`
	Digest        string                          `json:"digest"`
	Result        string                          `json:"result"`
	Constraints   []string                        `json:"constraints,omitempty"`
	Authorization *AuthorizationControlProvenance `json:"authorization,omitempty"`
	Admission     *AdmissionControlProvenance     `json:"admission,omitempty"`
	Provider      *ProviderControlProvenance      `json:"provider,omitempty"`
}

// The provenance shape is deliberately category-specific so callers cannot
// claim that a policy, admission decision, and provider check are equivalent.
type AuthorizationControlProvenance struct {
	Decision     AuthorizationControlResult `json:"decision,omitempty"`
	MatchedGrant string                     `json:"matched_grant,omitempty"`
	ReasonCode   string                     `json:"reason_code,omitempty"`
	Reason       string                     `json:"reason,omitempty"`
}

// AdmissionResult is the closed typed admission outcome; there is no implicit
// pass when an admission engine stays silent.
type AdmissionResult string

const (
	AdmissionAdmit  AdmissionResult = "admit"
	AdmissionReject AdmissionResult = "reject"
)

// AdmissionCheckStatus is the closed status of a single evaluated admission
// check.
type AdmissionCheckStatus string

const (
	AdmissionCheckPass    AdmissionCheckStatus = "pass"
	AdmissionCheckFail    AdmissionCheckStatus = "fail"
	AdmissionCheckUnknown AdmissionCheckStatus = "unknown"
)

// AdmissionEvaluatedCheck records one fixed check key with its typed result,
// a stable reason, and an optional evidence source.
type AdmissionEvaluatedCheck struct {
	Key      string               `json:"key"`
	Status   AdmissionCheckStatus `json:"status"`
	Reason   string               `json:"reason"`
	Evidence string               `json:"evidence"`
}

func (check AdmissionEvaluatedCheck) Validate() error {
	if check.Key == "" || !isAdmissionCheckStatus(check.Status) || check.Reason == "" {
		return fmt.Errorf("admission evaluated checks require a key, typed status, and reason")
	}
	return nil
}

// AdmissionControlProvenance preserves the exact rules and facts digests,
// evaluated checks, constraints, and typed admit/reject result of one
// execution-admission evaluation.
type AdmissionControlProvenance struct {
	AdmissionID     string                    `json:"admission_id"`
	RulesDigest     string                    `json:"rules_digest"`
	FactsDigest     string                    `json:"facts_digest"`
	Result          AdmissionResult           `json:"result"`
	EvaluatedChecks []AdmissionEvaluatedCheck `json:"evaluated_checks"`
	DecisionDigest  string                    `json:"decision_digest"`
}

func (provenance AdmissionControlProvenance) Validate() error {
	if provenance.AdmissionID == "" || provenance.RulesDigest == "" || provenance.FactsDigest == "" || provenance.DecisionDigest == "" || (provenance.Result != AdmissionAdmit && provenance.Result != AdmissionReject) {
		return fmt.Errorf("admission provenance requires identity, rules and facts digests, typed result, and decision digest")
	}
	if len(provenance.EvaluatedChecks) == 0 {
		return fmt.Errorf("admission provenance requires evaluated checks")
	}
	for _, check := range provenance.EvaluatedChecks {
		if err := check.Validate(); err != nil {
			return err
		}
	}
	return nil
}

type ProviderControlProvenance struct {
	ProviderID     string `json:"provider_id"`
	Release        string `json:"release"`
	ResponseDigest string `json:"response_digest"`
}

func (provenance ProviderControlProvenance) Validate() error {
	if provenance.ProviderID == "" || !isExactPackageRelease(provenance.Release) || !isSHA256Digest(provenance.ResponseDigest) {
		return fmt.Errorf("provider control provenance requires provider identity, exact release, and response digest")
	}
	return nil
}

func (control EvaluatedControl) Validate() error {
	if control.Kind == "" || control.ControlID == "" || control.Revision == "" || control.Digest == "" || !isControlResult(control.Result) {
		return fmt.Errorf("evaluated controls must retain typed provenance and a closed result")
	}
	switch control.Kind {
	case ControlAuthorization:
		if control.Authorization == nil || control.Admission != nil || control.Provider != nil {
			return fmt.Errorf("authorization control requires authorization provenance")
		}
		provenance := control.Authorization
		decision := provenance.Decision
		switch control.Result {
		case ControlResultAllowed:
			if decision != AuthorizationControlAllow || provenance.MatchedGrant == "" || provenance.ReasonCode != "" || provenance.Reason != "" {
				return fmt.Errorf("allowed authorization requires an allow decision and matched grant without denial provenance")
			}
		case ControlResultDenied:
			if decision != AuthorizationControlDeny || provenance.MatchedGrant != "" || provenance.ReasonCode == "" {
				return fmt.Errorf("denied authorization requires a typed denial reason and no matched grant")
			}
		default:
			return fmt.Errorf("authorization control result must be allowed or denied")
		}
	case ControlAdmission:
		if control.Admission == nil || control.Authorization != nil || control.Provider != nil {
			return fmt.Errorf("admission control requires admission provenance")
		}
		if control.Admission.Result == AdmissionAdmit && control.Result != ControlResultAllowed || control.Admission.Result == AdmissionReject && control.Result != ControlResultDenied {
			return fmt.Errorf("admission control result contradicts typed admission decision")
		}
		return control.Admission.Validate()
	case ControlProvider:
		if control.Provider == nil || control.Authorization != nil || control.Admission != nil {
			return fmt.Errorf("provider control requires provider provenance")
		}
		return control.Provider.Validate()
	default:
		return fmt.Errorf("evaluated control kind is invalid")
	}
	return nil
}

func isControlResult(result string) bool {
	switch result {
	case ControlResultAllowed, ControlResultDenied, ControlResultSucceeded, ControlResultFailed:
		return true
	default:
		return false
	}
}

type Redaction struct {
	Field  string          `json:"field"`
	Reason RedactionReason `json:"reason"`
}

const MaxAuditChangeSummaryBytes = 1024

// SafeSummaryCode is the closed template catalog for canonical summaries.
type SafeSummaryCode string

const (
	SafeSummaryCodeStateWrite             SafeSummaryCode = "state-write"
	SafeSummaryCodeExportProbe            SafeSummaryCode = "export-probe"
	SafeSummaryCodeTargetLifecycle        SafeSummaryCode = "target-lifecycle"
	SafeSummaryCodeAgentDefinitionPublish SafeSummaryCode = "agent-definition-publish"
)

// SafeSummaryValue is a closed, non-sensitive value catalog. There is no
// free-form caller-controlled message or arbitrary secret-shaped value.
type SafeSummaryValue string

const (
	SafeSummaryValueCompleted           SafeSummaryValue = "completed"
	SafeSummaryValueDeploymentCompleted SafeSummaryValue = "deployment completed"
	SafeSummaryValueSucceeded           SafeSummaryValue = "succeeded"
	SafeSummaryValueFailed              SafeSummaryValue = "failed"
	SafeSummaryValueCreated             SafeSummaryValue = "created"
	SafeSummaryValueUpdated             SafeSummaryValue = "updated"
	SafeSummaryValueEnabled             SafeSummaryValue = "enabled"
	SafeSummaryValueDisabled            SafeSummaryValue = "disabled"
	SafeSummaryValueAccepted            SafeSummaryValue = "accepted"
	SafeSummaryValueRejected            SafeSummaryValue = "rejected"
)

// SafeSummaryField is a bounded, non-sensitive detail in a canonical summary.
// Field names are closed below so callers cannot smuggle arbitrary key/value
// material into audit or probe wire DTOs.
type SafeSummaryField struct {
	Name  string           `json:"name"`
	Value SafeSummaryValue `json:"value"`
}

// SafeSummary is the only accepted summary representation for audit and probe
// DTOs. It has no free-form message field; values are independently typed and
// allowlisted before canonical JSON serialization.
type SafeSummary struct {
	Code   SafeSummaryCode    `json:"code"`
	Fields []SafeSummaryField `json:"fields,omitempty"`
}

var safeSummaryFieldNames = map[string]struct{}{
	"action": {}, "count": {}, "format": {}, "operation": {}, "reason": {},
	"resource": {}, "result": {}, "revision": {}, "scope": {}, "status": {}, "target": {},
}

func (summary SafeSummary) Validate() error {
	switch summary.Code {
	case SafeSummaryCodeStateWrite, SafeSummaryCodeExportProbe, SafeSummaryCodeTargetLifecycle, SafeSummaryCodeAgentDefinitionPublish:
	default:
		return fmt.Errorf("safe summary code is not in the closed catalog")
	}
	if len(summary.Fields) > 12 {
		return fmt.Errorf("safe summary has too many fields")
	}
	seen := make(map[string]struct{}, len(summary.Fields))
	for _, field := range summary.Fields {
		if _, ok := safeSummaryFieldNames[field.Name]; !ok {
			return fmt.Errorf("safe summary field %q is not allowlisted", field.Name)
		}
		if _, ok := seen[field.Name]; ok {
			return fmt.Errorf("safe summary field %q is duplicated", field.Name)
		}
		seen[field.Name] = struct{}{}
		switch field.Value {
		case SafeSummaryValueCompleted, SafeSummaryValueDeploymentCompleted, SafeSummaryValueSucceeded, SafeSummaryValueFailed, SafeSummaryValueCreated, SafeSummaryValueUpdated, SafeSummaryValueEnabled, SafeSummaryValueDisabled, SafeSummaryValueAccepted, SafeSummaryValueRejected:
		default:
			return fmt.Errorf("safe summary field %q has a value outside the closed catalog", field.Name)
		}
	}
	return nil
}

func (summary SafeSummary) MarshalJSON() ([]byte, error) {
	if err := summary.Validate(); err != nil {
		return nil, err
	}
	type safeSummaryWire SafeSummary
	return json.Marshal(safeSummaryWire(summary))
}

func validateChangeSummary(summary *SafeSummary) error {
	if summary == nil {
		return nil
	}
	return summary.Validate()
}

func cloneSafeSummary(summary *SafeSummary) *SafeSummary {
	if summary == nil {
		return nil
	}
	clone := *summary
	clone.Fields = append([]SafeSummaryField(nil), summary.Fields...)
	return &clone
}

// AuditEvent is the append-only local audit authority. It intentionally has no
// secret-bearing fields.
type AuditEvent struct {
	ID                string             `json:"audit_event_id"`
	ScopeID           string             `json:"scope_id"`
	ProjectID         string             `json:"project_id"`
	RequestContextID  string             `json:"request_context_id"`
	PrincipalID       string             `json:"principal_id"`
	Category          Category           `json:"category"`
	Action            string             `json:"action"`
	Subject           SubjectKey         `json:"subject"`
	Outcome           Outcome            `json:"outcome"`
	EvaluatedControls []EvaluatedControl `json:"evaluated_controls"`
	ChangeSummary     *SafeSummary       `json:"change_summary,omitempty"`
	OccurredAt        time.Time          `json:"occurred_at"`
	CausationID       string             `json:"causation_id"`
	CorrelationID     string             `json:"correlation_id"`
	Redactions        []Redaction        `json:"redactions,omitempty"`
}

func (event AuditEvent) MarshalJSON() ([]byte, error) {
	if err := event.Validate(); err != nil {
		return nil, err
	}
	event.OccurredAt = event.OccurredAt.UTC()
	type wireAuditEvent AuditEvent
	return json.Marshal(wireAuditEvent(event))
}

func (event AuditEvent) Validate() error {
	if event.ID == "" || event.ScopeID == "" || event.RequestContextID == "" || event.PrincipalID == "" || event.Action == "" || event.CausationID == "" || event.CorrelationID == "" {
		return fmt.Errorf("audit event identity, scope, request context, action, causation, and correlation are required")
	}
	if !isCategory(event.Category) || !isOutcome(event.Outcome) || event.OccurredAt.IsZero() {
		return fmt.Errorf("audit event category, outcome, and occurred time must be valid")
	}
	if err := validateChangeSummary(event.ChangeSummary); err != nil {
		return err
	}
	if event.Subject.Kind == "" || event.Subject.ID == "" {
		return fmt.Errorf("audit event subject kind and id are required")
	}
	if len(event.EvaluatedControls) == 0 {
		return fmt.Errorf("audit event requires evaluated controls")
	}
	type controlIdentity struct {
		kind ControlKind
		id   string
	}
	seenControlIdentities := make(map[controlIdentity]struct{}, len(event.EvaluatedControls))
	for _, control := range event.EvaluatedControls {
		if control.Kind == "" {
			return fmt.Errorf("audit event requires typed evaluated control provenance")
		}
		identity := controlIdentity{kind: control.Kind, id: control.ControlID}
		if _, duplicate := seenControlIdentities[identity]; duplicate {
			return fmt.Errorf("audit event contains a duplicate evaluated control identity")
		}
		seenControlIdentities[identity] = struct{}{}
		if err := control.Validate(); err != nil {
			return err
		}
	}
	for _, redaction := range event.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("redactions require a field and supported reason")
		}
	}
	return nil
}

// Summary creates the immutable redacted event projection and retains the
// control identities needed to anchor detail evidence to this event.
func (event AuditEvent) Summary() (AuditEventSummary, error) {
	if err := event.Validate(); err != nil {
		return AuditEventSummary{}, err
	}
	snapshot := cloneAuditEvent(event)
	anchors := make([]EvaluatedControlAnchor, 0, len(snapshot.EvaluatedControls))
	for _, control := range snapshot.EvaluatedControls {
		anchors = append(anchors, controlAnchor(control))
	}
	summary := AuditEventSummary{ID: snapshot.ID, ScopeID: snapshot.ScopeID, ProjectID: snapshot.ProjectID, RequestContextID: snapshot.RequestContextID, PrincipalID: snapshot.PrincipalID, Category: snapshot.Category, Action: snapshot.Action, Subject: snapshot.Subject, Outcome: snapshot.Outcome, ChangeSummary: cloneSafeSummary(snapshot.ChangeSummary), OccurredAt: snapshot.OccurredAt, CausationID: snapshot.CausationID, CorrelationID: snapshot.CorrelationID, EvaluatedControlAnchors: anchors, Redactions: append([]Redaction(nil), snapshot.Redactions...)}
	if err := summary.Validate(); err != nil {
		return AuditEventSummary{}, err
	}
	return summary, nil
}

type auditAuthorityControl struct {
	Kind          ControlKind                     `json:"kind"`
	ControlID     string                          `json:"control_id"`
	Revision      string                          `json:"revision"`
	Digest        string                          `json:"digest"`
	Result        string                          `json:"result"`
	Constraints   []string                        `json:"constraints,omitempty"`
	Authorization *AuthorizationControlProvenance `json:"authorization,omitempty"`
	Admission     *AdmissionControlProvenance     `json:"admission,omitempty"`
	Provider      *ProviderControlProvenance      `json:"provider,omitempty"`
}

type auditAuthorityPayload struct {
	ID                string                  `json:"audit_event_id"`
	ScopeID           string                  `json:"scope_id"`
	ProjectID         string                  `json:"project_id"`
	RequestContextID  string                  `json:"request_context_id"`
	PrincipalID       string                  `json:"principal_id"`
	Category          Category                `json:"category"`
	Action            string                  `json:"action"`
	Subject           SubjectKey              `json:"subject"`
	Outcome           Outcome                 `json:"outcome"`
	EvaluatedControls []auditAuthorityControl `json:"evaluated_controls"`
	ChangeSummary     *SafeSummary            `json:"change_summary,omitempty"`
	OccurredAt        time.Time               `json:"occurred_at"`
	CausationID       string                  `json:"causation_id"`
	CorrelationID     string                  `json:"correlation_id"`
	Redactions        []Redaction             `json:"redactions,omitempty"`
}

func auditEventAuthorityDigest(event AuditEvent) string {
	controls := make([]auditAuthorityControl, len(event.EvaluatedControls))
	for index, control := range event.EvaluatedControls {
		controls[index] = auditAuthorityControl{
			Kind: control.Kind, ControlID: control.ControlID, Revision: control.Revision, Digest: control.Digest,
			Result: control.Result, Constraints: append([]string(nil), control.Constraints...),
			Authorization: control.Authorization, Admission: control.Admission, Provider: control.Provider,
		}
	}
	payload, _ := json.Marshal(auditAuthorityPayload{
		ID: event.ID, ScopeID: event.ScopeID, ProjectID: event.ProjectID, RequestContextID: event.RequestContextID,
		PrincipalID: event.PrincipalID, Category: event.Category, Action: event.Action, Subject: event.Subject,
		Outcome: event.Outcome, EvaluatedControls: controls, ChangeSummary: cloneSafeSummary(event.ChangeSummary), OccurredAt: event.OccurredAt.UTC(),
		CausationID: event.CausationID, CorrelationID: event.CorrelationID, Redactions: event.Redactions,
	})
	sum := sha256.Sum256(payload)
	return fmt.Sprintf("sha256:%x", sum[:])
}

func cloneAuditEvent(event AuditEvent) AuditEvent {
	clone := event
	clone.EvaluatedControls = make([]EvaluatedControl, len(event.EvaluatedControls))
	for index, control := range event.EvaluatedControls {
		copyControl := control
		copyControl.Constraints = append([]string(nil), control.Constraints...)
		if control.Authorization != nil {
			provenance := *control.Authorization
			copyControl.Authorization = &provenance
		}
		if control.Admission != nil {
			provenance := *control.Admission
			provenance.EvaluatedChecks = append([]AdmissionEvaluatedCheck(nil), control.Admission.EvaluatedChecks...)
			copyControl.Admission = &provenance
		}
		if control.Provider != nil {
			provenance := *control.Provider
			copyControl.Provider = &provenance
		}
		clone.EvaluatedControls[index] = copyControl
	}
	clone.Redactions = append([]Redaction(nil), event.Redactions...)
	clone.ChangeSummary = cloneSafeSummary(event.ChangeSummary)
	return clone
}

type TimeRange struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
}

func (timeRange TimeRange) Validate() error {
	if timeRange.Start.IsZero() || timeRange.End.IsZero() || !timeRange.Start.Before(timeRange.End) {
		return fmt.Errorf("time range must have a non-empty start and end")
	}
	return nil
}

type AuditEventFilter struct {
	TimeRange     TimeRange   `json:"time_range"`
	ProjectID     string      `json:"project_id"`
	Category      Category    `json:"category"`
	Action        string      `json:"action"`
	Outcome       Outcome     `json:"outcome"`
	PrincipalID   string      `json:"principal_id"`
	Subject       *SubjectKey `json:"subject"`
	CorrelationID string      `json:"correlation_id"`
}

func (filter AuditEventFilter) Validate() error {
	if err := filter.TimeRange.Validate(); err != nil {
		return err
	}
	if filter.Category != "" && !isCategory(filter.Category) {
		return fmt.Errorf("audit category filter is invalid")
	}
	if filter.Outcome != "" && !isOutcome(filter.Outcome) {
		return fmt.Errorf("audit outcome filter is invalid")
	}
	if filter.Subject != nil && (filter.Subject.Kind == "" || filter.Subject.ID == "") {
		return fmt.Errorf("subject filter requires kind and id")
	}
	return nil
}

// filterAccepts reports whether a redacted summary belongs to the bound
// filter. Every summary returned by a validated query must satisfy the same
// filter that authorized its cursor.
func filterAccepts(filter AuditEventFilter, item AuditEventSummary) bool {
	if item.OccurredAt.Before(filter.TimeRange.Start) || !item.OccurredAt.Before(filter.TimeRange.End) {
		return false
	}
	if filter.ProjectID != "" && item.ProjectID != filter.ProjectID {
		return false
	}
	if filter.Category != "" && item.Category != filter.Category {
		return false
	}
	if filter.Action != "" && item.Action != filter.Action {
		return false
	}
	if filter.Outcome != "" && item.Outcome != filter.Outcome {
		return false
	}
	if filter.PrincipalID != "" && item.PrincipalID != filter.PrincipalID {
		return false
	}
	if filter.Subject != nil && (item.Subject.Kind != filter.Subject.Kind || item.Subject.ID != filter.Subject.ID || (filter.Subject.Digest != "" && item.Subject.Digest != filter.Subject.Digest)) {
		return false
	}
	if filter.CorrelationID != "" && item.CorrelationID != filter.CorrelationID {
		return false
	}
	return true
}

func precedesAuditSummary(left, right AuditEventSummary) bool {
	if !left.OccurredAt.Equal(right.OccurredAt) {
		return left.OccurredAt.After(right.OccurredAt)
	}
	return left.ID > right.ID
}

// followsAuditPosition reports whether an item is strictly after the
// authenticated keyset position in the stable descending order.
func followsAuditPosition(item AuditEventSummary, seek CursorPosition) bool {
	if item.OccurredAt.Before(seek.OccurredAt) {
		return true
	}
	if item.OccurredAt.Equal(seek.OccurredAt) {
		return item.ID < seek.RecordID
	}
	return false
}

type QueryPolicy struct {
	MaxPageSize  int           `json:"max_page_size"`
	MaxTimeRange time.Duration `json:"max_time_range"`
	CursorCodec  CursorCodec   `json:"cursor_codec"`
}

// CursorCodec verifies opaque cursors using server-owned key material. The
// contract deliberately does not provide a signer or key: callers must inject
// the deployment implementation when validating a resumed page.
type CursorCodec interface {
	ValidateCursor(cursor string, binding CursorBinding) (CursorPosition, error)
	IssueCursor(binding CursorBinding, position CursorPosition) (string, error)
}

type CursorBinding struct {
	Version           string `json:"version"`
	ScopeID           string `json:"scope_id"`
	FilterFingerprint string `json:"filter_fingerprint"`
}

// CursorPosition is authenticated by CursorCodec as part of the opaque token.
type CursorPosition struct {
	OccurredAt time.Time `json:"occurred_at"`
	RecordID   string    `json:"record_id"`
}

const CursorVersion = "v1"

func DefaultQueryPolicy() QueryPolicy {
	return QueryPolicy{MaxPageSize: 500, MaxTimeRange: 31 * 24 * time.Hour}
}

type QueryAuditEventsRequest struct {
	RequestContext requestcontext.RequestContext `json:"request_context"`
	Filter         AuditEventFilter              `json:"filter"`
	Cursor         string                        `json:"cursor"`
	PageSize       int                           `json:"page_size"`
}

func (request QueryAuditEventsRequest) Validate() error {
	return request.ValidateWithPolicy(DefaultQueryPolicy())
}

func (request QueryAuditEventsRequest) ValidateWithPolicy(policy QueryPolicy) error {
	_, err := request.ValidatedWithPolicy(policy)
	return err
}

// ValidatedAuditEventsQuery carries the server-authenticated keyset position
// into query execution instead of discarding it after cursor validation. All
// state is immutable after validation; consumers must use the read-only
// accessors so a validated filter and seek cannot be mutated between
// authorization and execution.
type ValidatedAuditEventsQuery struct {
	request   QueryAuditEventsRequest
	seek      *CursorPosition
	binding   CursorBinding
	codec     CursorCodec
	policy    QueryPolicy
	validated bool
}

// Request returns a copy of the validated query request.
func (query ValidatedAuditEventsQuery) Request() QueryAuditEventsRequest {
	return cloneAuditRequest(query.request)
}

// Seek returns a copy of the authenticated keyset position, or nil for the
// first page.
func (query ValidatedAuditEventsQuery) Seek() *CursorPosition {
	if query.seek == nil {
		return nil
	}
	copy := *query.seek
	return &copy
}

func (query ValidatedAuditEventsQuery) ScopeID() string { return query.request.RequestContext.ScopeID }
func (query ValidatedAuditEventsQuery) Filter() AuditEventFilter {
	return cloneAuditRequest(query.request).Filter
}
func (query ValidatedAuditEventsQuery) PageSize() int            { return query.request.PageSize }
func (query ValidatedAuditEventsQuery) Binding() CursorBinding   { return query.binding }
func (query ValidatedAuditEventsQuery) CursorCodec() CursorCodec { return query.codec }

func (request QueryAuditEventsRequest) ValidatedWithPolicy(policy QueryPolicy) (ValidatedAuditEventsQuery, error) {
	if err := request.RequestContext.Validate(); err != nil || request.PageSize <= 0 || policy.MaxPageSize <= 0 || policy.MaxTimeRange <= 0 {
		return ValidatedAuditEventsQuery{}, fmt.Errorf("trusted request context and positive page size are required")
	}
	if err := request.Filter.Validate(); err != nil {
		return ValidatedAuditEventsQuery{}, err
	}
	if request.PageSize > policy.MaxPageSize || request.Filter.TimeRange.End.Sub(request.Filter.TimeRange.Start) > policy.MaxTimeRange {
		return ValidatedAuditEventsQuery{}, fmt.Errorf("query exceeds server pagination policy")
	}
	scopeID := request.RequestContext.ScopeID
	binding, err := cursorBinding(scopeID, request.Filter)
	if err != nil {
		return ValidatedAuditEventsQuery{}, err
	}
	position, err := validateCursor(request.Cursor, binding, policy.CursorCodec)
	if err != nil {
		return ValidatedAuditEventsQuery{}, err
	}
	request = cloneAuditRequest(request)
	return ValidatedAuditEventsQuery{request: request, seek: position, binding: binding, codec: policy.CursorCodec, policy: policy, validated: true}, nil
}

func cloneAuditRequest(request QueryAuditEventsRequest) QueryAuditEventsRequest {
	if request.Filter.Subject != nil {
		copy := *request.Filter.Subject
		request.Filter.Subject = &copy
	}
	return request
}

// NextCursor derives a cursor only from a page that passes the validated
// query's scope, filter, ordering, seek, size, and current-cursor checks. A
// standalone summary is deliberately not accepted because it may not be the
// actual final item returned by the query.
func (query ValidatedAuditEventsQuery) NextCursor(page AuditEventPage) (string, error) {
	if !query.validated {
		return "", fmt.Errorf("query must be validated before issuing a cursor")
	}
	if query.codec == nil {
		return "", fmt.Errorf("server cursor codec is required to issue a next page")
	}
	if err := page.Validate(query, query.policy); err != nil {
		return "", err
	}
	if len(page.Items) == 0 {
		return "", fmt.Errorf("cannot issue a cursor for an empty page")
	}
	last := page.Items[len(page.Items)-1]
	return query.codec.IssueCursor(query.binding, CursorPosition{OccurredAt: last.OccurredAt.UTC(), RecordID: last.ID})
}

type AuditEventSummary struct {
	ID                      string                   `json:"audit_event_id"`
	ScopeID                 string                   `json:"scope_id"`
	ProjectID               string                   `json:"project_id"`
	RequestContextID        string                   `json:"request_context_id"`
	PrincipalID             string                   `json:"principal_id"`
	Category                Category                 `json:"category"`
	Action                  string                   `json:"action"`
	Subject                 SubjectKey               `json:"subject"`
	Outcome                 Outcome                  `json:"outcome"`
	ChangeSummary           *SafeSummary             `json:"change_summary,omitempty"`
	OccurredAt              time.Time                `json:"occurred_at"`
	CausationID             string                   `json:"causation_id"`
	CorrelationID           string                   `json:"correlation_id"`
	EvaluatedControlAnchors []EvaluatedControlAnchor `json:"evaluated_control_anchors,omitempty"`
	Redactions              []Redaction              `json:"redactions"`
}

func (summary AuditEventSummary) Validate() error {
	if summary.ID == "" || summary.ScopeID == "" || summary.RequestContextID == "" || summary.PrincipalID == "" || summary.Action == "" || summary.Subject.Kind == "" || summary.Subject.ID == "" || !isCategory(summary.Category) || !isOutcome(summary.Outcome) || summary.OccurredAt.IsZero() || summary.CausationID == "" || summary.CorrelationID == "" {
		return fmt.Errorf("audit event summary requires redacted identity, request context, causal identity, subject, type, and time")
	}
	if err := validateChangeSummary(summary.ChangeSummary); err != nil {
		return err
	}
	for _, anchor := range summary.EvaluatedControlAnchors {
		if err := anchor.Validate(); err != nil {
			return err
		}
	}
	for _, redaction := range summary.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("audit event summary redactions are invalid")
		}
	}
	return nil
}

func (summary AuditEventSummary) MarshalJSON() ([]byte, error) {
	if err := summary.Validate(); err != nil {
		return nil, err
	}
	summary.OccurredAt = summary.OccurredAt.UTC()
	type auditEventSummaryWire AuditEventSummary
	return json.Marshal(auditEventSummaryWire(summary))
}

// AuditEventPage is one validated page of a stable keyset query. Validate
// must run before NextCursor is derived from the page so that the returned
// page provably follows the authenticated seek and bound filter.
type AuditEventPage struct {
	Items      []AuditEventSummary `json:"items"`
	NextCursor string              `json:"next_cursor"`
}

// Validate binds the page to the validated query: every item stays inside the
// bound scope, filter, time range, and page size; items are in stable
// descending order with no duplicates; the page follows the authenticated
// seek; and NextCursor is exactly the cursor of the actual final item.
func (page AuditEventPage) Validate(query ValidatedAuditEventsQuery, policy QueryPolicy) error {
	if !query.validated {
		return fmt.Errorf("query must be validated before validating a page")
	}
	request := query.request
	if request.PageSize <= 0 || policy.MaxPageSize <= 0 || policy.MaxTimeRange <= 0 {
		return fmt.Errorf("query policy must bound page size and time range")
	}
	if len(page.Items) == 0 {
		if page.NextCursor != "" {
			return fmt.Errorf("next cursor requires a non-empty page")
		}
		return nil
	}
	if len(page.Items) > request.PageSize || request.PageSize > policy.MaxPageSize {
		return fmt.Errorf("page exceeds validated page size")
	}
	seen := make(map[string]struct{}, len(page.Items))
	for index := range page.Items {
		item := page.Items[index]
		if err := item.Validate(); err != nil {
			return err
		}
		if item.ScopeID != request.RequestContext.ScopeID {
			return fmt.Errorf("audit event page item is outside the validated scope")
		}
		if !filterAccepts(request.Filter, item) {
			return fmt.Errorf("audit event page item is outside the authenticated filter")
		}
		if _, duplicate := seen[item.ID]; duplicate {
			return fmt.Errorf("audit event page contains a duplicate item")
		}
		seen[item.ID] = struct{}{}
		if index > 0 && !precedesAuditSummary(page.Items[index-1], item) {
			return fmt.Errorf("audit event page must use %s", AuditEventSortOrder)
		}
	}
	if query.seek != nil {
		if !followsAuditPosition(page.Items[0], *query.seek) {
			return fmt.Errorf("audit event page does not follow the authenticated seek")
		}
	}
	if page.NextCursor != "" {
		if query.codec == nil {
			return fmt.Errorf("server cursor codec is required to validate next cursor")
		}
		last := page.Items[len(page.Items)-1]
		expected, err := query.codec.IssueCursor(query.binding, CursorPosition{OccurredAt: last.OccurredAt.UTC(), RecordID: last.ID})
		if err != nil {
			return err
		}
		if page.NextCursor != expected {
			return fmt.Errorf("next cursor must be issued from the validated actual final item")
		}
	}
	return nil
}

type RelatedRecordAvailability string

const (
	AvailabilityAvailable     RelatedRecordAvailability = "available"
	AvailabilityUnavailable   RelatedRecordAvailability = "unavailable"
	AvailabilityRedacted      RelatedRecordAvailability = "redacted"
	AvailabilityNotAuthorized RelatedRecordAvailability = "not_authorized"
)

type AvailabilityReason string

const (
	AvailabilityReasonMissing       AvailabilityReason = "missing"
	AvailabilityReasonRetention     AvailabilityReason = "retention"
	AvailabilityReasonNotAuthorized AvailabilityReason = "not_authorized"
	AvailabilityReasonUnavailable   AvailabilityReason = "unavailable"
	AvailabilityReasonMapperFailure AvailabilityReason = "mapper_failure"
	AvailabilityReasonRedacted      AvailabilityReason = "redacted"
)

func isAvailabilityReason(reason AvailabilityReason) bool {
	switch reason {
	case AvailabilityReasonMissing, AvailabilityReasonRetention, AvailabilityReasonNotAuthorized, AvailabilityReasonUnavailable, AvailabilityReasonMapperFailure, AvailabilityReasonRedacted:
		return true
	default:
		return false
	}
}

func validateAvailabilityMetadata(availability RelatedRecordAvailability, reason AvailabilityReason, redactions []Redaction) error {
	if !isAvailability(availability) {
		return fmt.Errorf("availability state is invalid")
	}
	if reason != "" && !isAvailabilityReason(reason) {
		return fmt.Errorf("availability reason is invalid")
	}
	if err := validateRedactions(redactions); err != nil {
		return err
	}
	switch availability {
	case AvailabilityAvailable:
		if reason != "" || len(redactions) != 0 {
			return fmt.Errorf("available related records cannot carry an availability reason or redactions")
		}
	case AvailabilityUnavailable:
		if reason != AvailabilityReasonMissing && reason != AvailabilityReasonRetention && reason != AvailabilityReasonUnavailable && reason != AvailabilityReasonMapperFailure {
			return fmt.Errorf("unavailable related records require a structured missing, retention, or unavailable reason")
		}
		if len(redactions) != 0 {
			return fmt.Errorf("unavailable related records use a whole-record reason")
		}
	case AvailabilityRedacted:
		if (reason != AvailabilityReasonRedacted && reason != AvailabilityReasonRetention) || len(redactions) == 0 {
			return fmt.Errorf("redacted related records require a structured reason and redactions")
		}
	case AvailabilityNotAuthorized:
		if reason != AvailabilityReasonNotAuthorized || len(redactions) == 0 {
			return fmt.Errorf("not-authorized related records require a structured reason and redactions")
		}
	}
	return nil
}

type RelatedGovernanceRecord struct {
	Kind         string                    `json:"kind"`
	ID           string                    `json:"id"`
	Availability RelatedRecordAvailability `json:"availability"`
	Reason       AvailabilityReason        `json:"reason,omitempty"`
	Redactions   []Redaction               `json:"redactions"`
}

type RelatedSubject struct {
	Subject      SubjectKey                `json:"subject"`
	Availability RelatedRecordAvailability `json:"availability"`
	Reason       AvailabilityReason        `json:"reason,omitempty"`
	Redactions   []Redaction               `json:"redactions"`
}

// CredentialPurpose is the closed catalog of governed credential purposes.
// Credential evidence never discloses material; it only names the binding,
// its fingerprint, the exact target, and the outcome.
type CredentialPurpose string

const (
	CredentialPurposeAuditExport      CredentialPurpose = "audit-export"
	CredentialPurposeModelProvider    CredentialPurpose = "model-provider"
	CredentialPurposeMCPServer        CredentialPurpose = "mcp-server"
	CredentialPurposeGitHubRepository CredentialPurpose = "github-repository"
	CredentialPurposeDeploymentTarget CredentialPurpose = "deployment-target"
)

func isCredentialPurpose(purpose CredentialPurpose) bool {
	switch purpose {
	case CredentialPurposeAuditExport, CredentialPurposeModelProvider, CredentialPurposeMCPServer, CredentialPurposeGitHubRepository, CredentialPurposeDeploymentTarget:
		return true
	default:
		return false
	}
}

// CredentialUseEvidence identifies a credential use without ever disclosing
// material from the credential itself.
type CredentialUseEvidence struct {
	BindingID    string                    `json:"binding_id"`
	Fingerprint  string                    `json:"fingerprint"`
	Purpose      CredentialPurpose         `json:"purpose"`
	Target       string                    `json:"target"`
	Outcome      string                    `json:"outcome"`
	Availability RelatedRecordAvailability `json:"availability"`
	Redactions   []Redaction               `json:"redactions"`
}

var credentialEvidenceFields = []string{"binding_id", "fingerprint", "target", "outcome"}

func isSafeCredentialIdentifier(value string, allowSlash bool) bool {
	if value == "" || len(value) > 256 || strings.TrimSpace(value) != value || containsSensitiveMaterial(value) {
		return false
	}
	for _, character := range value {
		if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || character == '-' || character == '_' || character == '.' || character == ':' || (allowSlash && character == '/') {
			continue
		}
		return false
	}
	return true
}

func isSafeCredentialFingerprint(value string) bool {
	if isSHA256Digest(value) {
		return true
	}
	if !strings.HasPrefix(value, "sha256:") || containsSensitiveMaterial(value) {
		return false
	}
	suffix := strings.TrimPrefix(value, "sha256:")
	if suffix == "" || len(suffix) > 64 {
		return false
	}
	for _, character := range suffix {
		if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || character == '-' {
			continue
		}
		return false
	}
	return true
}

func isSafeCredentialOutcome(value string) bool {
	return value == string(OutcomeSucceeded) || value == string(OutcomeFailed)
}

func (use CredentialUseEvidence) Validate() error {
	if !isCredentialPurpose(use.Purpose) || !isAvailability(use.Availability) {
		return fmt.Errorf("credential use evidence requires a supported purpose and availability")
	}
	values := map[string]string{
		"binding_id":  use.BindingID,
		"fingerprint": use.Fingerprint,
		"target":      use.Target,
		"outcome":     use.Outcome,
	}
	if use.BindingID != "" && !isSafeCredentialIdentifier(use.BindingID, false) {
		return fmt.Errorf("credential use binding id is not a safe identifier")
	}
	if use.Fingerprint != "" && !isSafeCredentialFingerprint(use.Fingerprint) {
		return fmt.Errorf("credential use fingerprint is not a safe fingerprint")
	}
	if use.Target != "" && !isSafeCredentialIdentifier(use.Target, true) {
		return fmt.Errorf("credential use target is not a safe identifier")
	}
	if use.Outcome != "" && !isSafeCredentialOutcome(use.Outcome) {
		return fmt.Errorf("credential use outcome is not supported")
	}
	redactions := make(map[string]RedactionReason, len(use.Redactions))
	for _, redaction := range use.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("credential use redactions are invalid")
		}
		if _, known := values[redaction.Field]; !known {
			return fmt.Errorf("credential use redaction field %q is not supported", redaction.Field)
		}
		if _, duplicate := redactions[redaction.Field]; duplicate {
			return fmt.Errorf("credential use redaction field %q is duplicated", redaction.Field)
		}
		if values[redaction.Field] != "" {
			return fmt.Errorf("credential use field %q cannot be populated and redacted", redaction.Field)
		}
		redactions[redaction.Field] = redaction.Reason
	}
	if use.Availability == AvailabilityAvailable {
		for _, field := range credentialEvidenceFields {
			if values[field] == "" {
				return fmt.Errorf("available credential use evidence requires %s", field)
			}
		}
		if len(redactions) != 0 {
			return fmt.Errorf("available credential use evidence cannot contain redactions")
		}
		return nil
	}
	if len(redactions) == 0 {
		return fmt.Errorf("credential use evidence requires field-level redactions for unavailable fields")
	}
	for _, field := range credentialEvidenceFields {
		reason, omitted := redactions[field]
		if values[field] == "" && !omitted {
			return fmt.Errorf("credential use evidence omits %s without a redaction", field)
		}
		if !omitted {
			continue
		}
		switch use.Availability {
		case AvailabilityNotAuthorized:
			if reason != RedactionNotAuthorized {
				return fmt.Errorf("not-authorized credential use field %s must use not_authorized redaction", field)
			}
		case AvailabilityRedacted:
			if reason != RedactionSecret && reason != RedactionRetention {
				return fmt.Errorf("redacted credential use field %s must use secret or retention redaction", field)
			}
		case AvailabilityUnavailable:
			if reason != RedactionRetention {
				return fmt.Errorf("unavailable credential use field %s must use retention redaction", field)
			}
		}
	}
	return nil
}

func (use CredentialUseEvidence) MarshalJSON() ([]byte, error) {
	if err := use.Validate(); err != nil {
		return nil, err
	}
	type credentialUseEvidenceWire CredentialUseEvidence
	return json.Marshal(credentialUseEvidenceWire(use))
}

// RequestContextSummary is the redacted request context projection. RequestID
// identifies the incoming request; RequestContextID identifies the persisted
// context record referenced by AuditEvent.RequestContextID.
type RequestContextSummary struct {
	PrincipalID      string `json:"principal_id"`
	ScopeID          string `json:"scope_id"`
	Source           string `json:"source"`
	RequestContextID string `json:"request_context_id"`
	RequestID        string `json:"request_id"`
	CausationID      string `json:"causation_id"`
	CorrelationID    string `json:"correlation_id"`
}

type EvaluatedControlAnchor struct {
	Kind      ControlKind `json:"kind"`
	ControlID string      `json:"control_id"`
	Revision  string      `json:"revision"`
	Digest    string      `json:"digest"`
}

func (anchor EvaluatedControlAnchor) Validate() error {
	if anchor.Kind == "" || anchor.ControlID == "" || anchor.Revision == "" || anchor.Digest == "" {
		return fmt.Errorf("evaluated control anchor requires kind, identity, revision, and digest")
	}
	return nil
}

func controlAnchor(control EvaluatedControl) EvaluatedControlAnchor {
	return EvaluatedControlAnchor{Kind: control.Kind, ControlID: control.ControlID, Revision: control.Revision, Digest: control.Digest}
}

func (contextSummary RequestContextSummary) Validate() error {
	if contextSummary.PrincipalID == "" || contextSummary.ScopeID == "" || contextSummary.Source == "" || contextSummary.RequestContextID == "" || contextSummary.RequestID == "" || contextSummary.CausationID == "" || contextSummary.CorrelationID == "" {
		return fmt.Errorf("redacted request context requires principal, scope, persisted context, request, causation, and correlation identity")
	}
	return nil
}

// AuditEventDetailView is a request-time DTO anchored on the immutable
// AuditEvent. It must retain the request-context identity, redacted request
// context, and the full category-specific evaluated-control evidence chain.
type AuditEventDetailView struct {
	Event                    AuditEventSummary         `json:"event"`
	RequestContextSummary    RequestContextSummary     `json:"request_context_summary"`
	EvaluatedControls        []EvaluatedControl        `json:"evaluated_controls"`
	CredentialUses           []CredentialUseEvidence   `json:"credential_uses"`
	RelatedGovernanceRecords []RelatedGovernanceRecord `json:"related_governance_records"`
	RelatedSubjects          []RelatedSubject          `json:"related_subjects"`
	Redactions               []Redaction               `json:"redactions"`
	authoritativeEvent       *AuditEvent               `json:"-"`
	authorityDigest          string                    `json:"-"`
}

// AuditEventStoreBinding is an opaque binding issued by the authoritative
// audit store. Its event and full-provenance digest are intentionally private;
// a serialized summary cannot manufacture a detail authority token.
type auditStoreProof struct{}

type AuditEventStoreBinding struct {
	event  AuditEvent
	digest string
	proof  *auditStoreProof
}

// AuditEventStore is a sealed capability used by the detail constructor. The
// implementation is created from an opaque receipt issued by the platform/store
// wiring package; callers cannot provide an authority or trust root.
type AuditEventStore interface {
	auditEventStoreCapability()
	loadAuditEventBinding(context.Context, string) (AuditEventStoreBinding, error)
}

// AuditEventStoreCapability adapts one platform-issued receipt to the sealed
// detail port. The receipt type comes from an internal import boundary, so a
// public request package cannot construct or substitute it.
type AuditEventStoreCapability struct {
	receipt *auditstore.Receipt
}

// NewAuditEventStoreCapability accepts only a platform/store-issued receipt.
// Its internal parameter intentionally prevents external callers from naming
// or manufacturing a usable capability; store wiring obtains receipts after
// loading and verifying the immutable persisted record.
func NewAuditEventStoreCapability(receipt *auditstore.Receipt) AuditEventStoreCapability {
	return AuditEventStoreCapability{receipt: receipt}
}

func (AuditEventStoreCapability) auditEventStoreCapability() {}

func (store AuditEventStoreCapability) loadAuditEventBinding(ctx context.Context, eventID string) (AuditEventStoreBinding, error) {
	if ctx == nil {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store context is required")
	}
	if store.receipt == nil {
		return AuditEventStoreBinding{}, auditstore.ErrInvalidReceipt
	}
	payload, err := store.receipt.Payload(eventID)
	if err != nil {
		return AuditEventStoreBinding{}, err
	}
	var event AuditEvent
	if err := json.Unmarshal(payload, &event); err != nil {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store receipt payload is invalid: %w", err)
	}
	if event.ID != eventID {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store receipt identity does not match requested event")
	}
	return issueAuditEventStoreBinding(event, &auditStoreProof{})
}

// LoadAuditEventStoreBinding asks the authority-owned store for the immutable
// record and converts its verified receipt into an opaque binding. Request and
// browser DTOs never participate in issuance.
func LoadAuditEventStoreBinding(ctx context.Context, store AuditEventStore, eventID string) (AuditEventStoreBinding, error) {
	if store == nil || eventID == "" {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store and event identity are required")
	}
	if capability, ok := store.(*AuditEventStoreCapability); ok && capability == nil {
		return AuditEventStoreBinding{}, auditstore.ErrInvalidReceipt
	}
	binding, err := store.loadAuditEventBinding(ctx, eventID)
	if err != nil {
		return AuditEventStoreBinding{}, err
	}
	if err := binding.Validate(); err != nil {
		return AuditEventStoreBinding{}, err
	}
	if binding.event.ID != eventID {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store returned a different event identity")
	}
	return binding, nil
}

// issueAuditEventStoreBinding validates and snapshots an event at the store
// boundary. Only the authoritative audit-store adapter may issue this binding;
// consumers receive it from that adapter and cannot manufacture one from a DTO.
func issueAuditEventStoreBinding(event AuditEvent, proof *auditStoreProof) (AuditEventStoreBinding, error) {
	if proof == nil {
		return AuditEventStoreBinding{}, fmt.Errorf("audit event store proof is required")
	}
	snapshot := cloneAuditEvent(event)
	if err := snapshot.Validate(); err != nil {
		return AuditEventStoreBinding{}, err
	}
	return AuditEventStoreBinding{event: snapshot, digest: auditEventAuthorityDigest(snapshot), proof: proof}, nil
}

func (binding AuditEventStoreBinding) Validate() error {
	if binding.proof == nil {
		return fmt.Errorf("audit event store binding requires an authority-owned proof")
	}
	if binding.digest == "" {
		return fmt.Errorf("audit event store binding requires an opaque digest")
	}
	if err := binding.event.Validate(); err != nil {
		return err
	}
	if binding.digest != auditEventAuthorityDigest(binding.event) {
		return fmt.Errorf("audit event store binding digest does not match the immutable event")
	}
	return nil
}

// NewAuditEventDetailView rejects direct caller values. Use
// NewAuditEventDetailViewFromStore with a store-issued binding so forged event
// input cannot become the authority for a detail response.
func NewAuditEventDetailView(event AuditEvent) (AuditEventDetailView, error) {
	return AuditEventDetailView{}, fmt.Errorf("audit event detail requires a store-issued immutable binding")
}

// NewAuditEventDetailViewFromStore binds a detail response to a validated
// immutable event snapshot. The controls and anchors are derived internally;
// callers fill only request-time redacted evidence before Validate.
func NewAuditEventDetailViewFromStore(binding AuditEventStoreBinding) (AuditEventDetailView, error) {
	if err := binding.Validate(); err != nil {
		return AuditEventDetailView{}, err
	}
	snapshot := cloneAuditEvent(binding.event)
	summary, err := snapshot.Summary()
	if err != nil {
		return AuditEventDetailView{}, err
	}
	controls := cloneAuditEvent(snapshot).EvaluatedControls
	return AuditEventDetailView{Event: summary, EvaluatedControls: controls, authoritativeEvent: &snapshot, authorityDigest: binding.digest}, nil
}

// ValidateWithStore rebinds a serialized detail DTO to the immutable event
// loaded from the authoritative store before running the full detail checks.
func (view AuditEventDetailView) ValidateWithStore(binding AuditEventStoreBinding) error {
	if err := binding.Validate(); err != nil {
		return err
	}
	snapshot := cloneAuditEvent(binding.event)
	view.authoritativeEvent = &snapshot
	view.authorityDigest = binding.digest
	return view.Validate()
}

func (view AuditEventDetailView) MarshalJSON() ([]byte, error) {
	if err := view.Validate(); err != nil {
		return nil, err
	}
	type auditEventDetailViewWire AuditEventDetailView
	return json.Marshal(auditEventDetailViewWire(view))
}

func (view AuditEventDetailView) Validate() error {
	if view.authoritativeEvent == nil || view.authorityDigest == "" {
		return fmt.Errorf("audit event detail requires an authoritative immutable event binding")
	}
	authoritative := *view.authoritativeEvent
	if err := authoritative.Validate(); err != nil {
		return err
	}
	if auditEventAuthorityDigest(authoritative) != view.authorityDigest {
		return fmt.Errorf("audit event detail authority binding does not match the immutable event")
	}
	expected, err := authoritative.Summary()
	if err != nil {
		return err
	}
	if !auditSummaryMatches(view.Event, expected) {
		return fmt.Errorf("audit event detail summary is not bound to the authoritative event")
	}
	if err := view.Event.Validate(); err != nil {
		return err
	}
	if view.Event.RequestContextID == "" {
		return fmt.Errorf("audit event detail must retain the request context identity")
	}
	if err := view.RequestContextSummary.Validate(); err != nil {
		return err
	}
	if view.RequestContextSummary.RequestContextID != view.Event.RequestContextID || view.RequestContextSummary.ScopeID != view.Event.ScopeID || view.RequestContextSummary.PrincipalID != view.Event.PrincipalID || view.RequestContextSummary.CausationID != view.Event.CausationID || view.RequestContextSummary.CorrelationID != view.Event.CorrelationID {
		return fmt.Errorf("audit event detail persisted request context must match the audit event principal, scope, and causal anchor")
	}
	if len(view.EvaluatedControls) == 0 || len(view.Event.EvaluatedControlAnchors) != len(view.EvaluatedControls) {
		return fmt.Errorf("audit event detail requires evaluated controls anchored to the immutable event")
	}
	if !reflect.DeepEqual(view.EvaluatedControls, authoritative.EvaluatedControls) {
		return fmt.Errorf("audit event detail evaluated controls must match the complete immutable event provenance")
	}
	for index, control := range view.EvaluatedControls {
		if err := control.Validate(); err != nil {
			return err
		}
		anchor := view.Event.EvaluatedControlAnchors[index]
		if err := anchor.Validate(); err != nil {
			return err
		}
		if anchor != controlAnchor(control) {
			return fmt.Errorf("audit event detail evaluated control does not match the immutable event anchor")
		}
	}
	for _, record := range view.RelatedGovernanceRecords {
		if record.Kind == "" || record.ID == "" {
			return fmt.Errorf("related governance records require identity")
		}
		if err := validateAvailabilityMetadata(record.Availability, record.Reason, record.Redactions); err != nil {
			return err
		}
	}
	for _, subject := range view.RelatedSubjects {
		if subject.Subject.Kind == "" || subject.Subject.ID == "" {
			return fmt.Errorf("related subjects require identity")
		}
		if err := validateAvailabilityMetadata(subject.Availability, subject.Reason, subject.Redactions); err != nil {
			return err
		}
	}
	for _, use := range view.CredentialUses {
		if err := use.Validate(); err != nil {
			return err
		}
	}
	if err := validateRedactions(view.Redactions); err != nil {
		return err
	}
	return nil
}

func auditSummaryMatches(actual, expected AuditEventSummary) bool {
	return actual.ID == expected.ID && actual.ScopeID == expected.ScopeID && actual.ProjectID == expected.ProjectID && actual.RequestContextID == expected.RequestContextID && actual.PrincipalID == expected.PrincipalID && actual.Category == expected.Category && actual.Action == expected.Action && actual.Subject == expected.Subject && actual.Outcome == expected.Outcome && reflect.DeepEqual(actual.ChangeSummary, expected.ChangeSummary) && actual.OccurredAt.Equal(expected.OccurredAt) && actual.CausationID == expected.CausationID && actual.CorrelationID == expected.CorrelationID && reflect.DeepEqual(actual.EvaluatedControlAnchors, expected.EvaluatedControlAnchors) && reflect.DeepEqual(actual.Redactions, expected.Redactions)
}

type ExportFormat string

const (
	ExportFormatCSV   ExportFormat = "csv"
	ExportFormatJSONL ExportFormat = "jsonl"
)

// ExportAuditEventsRequest reuses the shared query filter set without any
// pagination cursor or page-size semantics. Exports are bounded by the
// server export policy (rows and bytes), not by keyset pagination.
type ExportAuditEventsRequest struct {
	RequestContext requestcontext.RequestContext `json:"request_context"`
	Filter         AuditEventFilter              `json:"filter"`
	Format         ExportFormat                  `json:"format"`
}

func (request ExportAuditEventsRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil {
		return fmt.Errorf("audit export requires a trusted request context: %w", err)
	}
	if err := request.Filter.Validate(); err != nil {
		return err
	}
	if request.Format != ExportFormatCSV && request.Format != ExportFormatJSONL {
		return fmt.Errorf("audit export format must be csv or jsonl")
	}
	return nil
}

type ExportPolicy struct {
	MaxRows            int                       `json:"max_rows"`
	MaxBytes           int                       `json:"max_bytes"`
	CredentialBindings CredentialBindingResolver `json:"credential_bindings"`
}

func DefaultExportPolicy() ExportPolicy { return ExportPolicy{MaxRows: 10000, MaxBytes: 32 << 20} }

func (request ExportAuditEventsRequest) ValidateWithPolicy(queryPolicy QueryPolicy, exportPolicy ExportPolicy) error {
	if exportPolicy.MaxRows <= 0 || exportPolicy.MaxBytes <= 0 {
		return fmt.Errorf("audit export policy must bound rows and bytes")
	}
	if err := request.Validate(); err != nil {
		return err
	}
	if queryPolicy.MaxTimeRange <= 0 || request.Filter.TimeRange.End.Sub(request.Filter.TimeRange.Start) > queryPolicy.MaxTimeRange {
		return fmt.Errorf("audit export exceeds server time range policy")
	}
	return nil
}

type ExportLimit string

const (
	ExportLimitRows  ExportLimit = "rows"
	ExportLimitBytes ExportLimit = "bytes"
)

type ExportLimitExceeded struct {
	Limit   ExportLimit `json:"limit"`
	Maximum int         `json:"maximum"`
}

func (failure ExportLimitExceeded) Validate() error {
	if (failure.Limit != ExportLimitRows && failure.Limit != ExportLimitBytes) || failure.Maximum <= 0 {
		return fmt.Errorf("export limit failure must identify a positive row or byte limit")
	}
	return nil
}

type AuditExportTarget struct {
	ID                  string                   `json:"audit_export_target_id"`
	ScopeID             string                   `json:"scope_id"`
	Name                string                   `json:"name"`
	Description         string                   `json:"description"`
	ExportType          ExportType               `json:"export_type"`
	Configuration       AuditExportConfiguration `json:"configuration"`
	ConfigurationDigest string                   `json:"configuration_digest"`
	CredentialBindingID string                   `json:"credential_binding_id"`
	Enabled             bool                     `json:"enabled"`
	Revision            string                   `json:"revision"`
}

func (target AuditExportTarget) MarshalJSON() ([]byte, error) {
	if err := target.Validate(); err != nil {
		return nil, err
	}
	type auditExportTargetWire AuditExportTarget
	return json.Marshal(auditExportTargetWire(target))
}

func (target AuditExportTarget) Validate() error {
	if target.ID == "" || target.ScopeID == "" || target.Name == "" || target.Description == "" || target.ExportType == "" || target.Revision == "" || target.ConfigurationDigest == "" {
		return fmt.Errorf("audit export target identity, configuration, and revision are required")
	}
	if err := target.Configuration.ValidateFor(target.ExportType); err != nil {
		return err
	}
	if target.ExportType.RequiresAuthentication() && target.CredentialBindingID == "" {
		return fmt.Errorf("audit export type %s requires a credential binding", target.ExportType)
	}
	if !target.ExportType.RequiresAuthentication() && target.CredentialBindingID != "" {
		return fmt.Errorf("audit export type %s does not permit a credential binding", target.ExportType)
	}
	digest, err := target.Configuration.DigestFor(target.ExportType, target.CredentialBindingID)
	if err != nil {
		return err
	}
	if target.ConfigurationDigest != digest {
		return fmt.Errorf("audit export target configuration digest does not match normalized configuration and binding")
	}
	return nil
}

func (target AuditExportTarget) ValidateWithPolicy(policy ExportPolicy) error {
	if err := target.Validate(); err != nil {
		return err
	}
	if target.CredentialBindingID == "" {
		return nil
	}
	// The binding subject is the target identity, not the caller principal:
	// proof that the credential is authorized for this exact export target.
	return validateAuthoritativeCredentialBinding(policy, target.CredentialBindingID, target.ScopeID, target.ExportType, target.ID)
}

// ExportType is the closed catalog of export implementations shipped by OAC.
type ExportType string

const (
	ExportTypeWebhook ExportType = "webhook"
	ExportTypeSyslog  ExportType = "syslog"
)

// RequiresAuthentication reports whether the built-in export type declares
// that delivery requires a CredentialBinding. Credentials are optional only
// for types that declare this requirement.
func (exportType ExportType) RequiresAuthentication() bool {
	spec, ok := exportSchemaForType(exportType)
	return ok && spec.requiresAuth
}

type ExportSchemaVersion string

const (
	ExportSchemaWebhookV1 ExportSchemaVersion = "webhook/v1"
	ExportSchemaSyslogV1  ExportSchemaVersion = "syslog/v1"
)

type ConfigurationField struct {
	Name  ConfigurationFieldName `json:"name"`
	Value string                 `json:"value"`
}

type ConfigurationFieldName string

const (
	ConfigurationFieldEndpoint ConfigurationFieldName = "endpoint"
	ConfigurationFieldIndex    ConfigurationFieldName = "index"
	ConfigurationFieldTenant   ConfigurationFieldName = "tenant"
)

type AuditExportConfiguration struct {
	SchemaVersion ExportSchemaVersion  `json:"schema_version"`
	Fields        []ConfigurationField `json:"fields"`
}

func (configuration AuditExportConfiguration) MarshalJSON() ([]byte, error) {
	if err := configuration.Validate(); err != nil {
		return nil, err
	}
	type auditExportConfigurationWire AuditExportConfiguration
	return json.Marshal(auditExportConfigurationWire(configuration))
}

func (configuration AuditExportConfiguration) Validate() error {
	spec, ok := exportSchemaForVersion(configuration.SchemaVersion)
	if !ok || len(configuration.Fields) == 0 {
		return fmt.Errorf("audit export configuration requires non-sensitive fields")
	}
	seen := make(map[ConfigurationFieldName]struct{}, len(configuration.Fields))
	for _, field := range configuration.Fields {
		if _, allowed := spec.allowedFields[field.Name]; !allowed || field.Value == "" || containsSensitiveMaterial(field.Value) {
			return fmt.Errorf("audit export configuration fields require names and values")
		}
		if _, exists := seen[field.Name]; exists {
			return fmt.Errorf("audit export configuration field names must be unique")
		}
		seen[field.Name] = struct{}{}
		if field.Name == ConfigurationFieldEndpoint && !validExportEndpoint(spec, field.Value) {
			return fmt.Errorf("audit export endpoint is invalid or contains credentials")
		}
	}
	if _, hasEndpoint := seen[ConfigurationFieldEndpoint]; !hasEndpoint {
		return fmt.Errorf("audit export configuration requires an endpoint")
	}
	return nil
}

func (configuration AuditExportConfiguration) ValidateFor(exportType ExportType) error {
	spec, ok := exportSchemaForType(exportType)
	if !ok || configuration.SchemaVersion != spec.version {
		return fmt.Errorf("audit export configuration schema must match export type")
	}
	return configuration.Validate()
}

// DigestFor hashes only the export type, the normalized non-sensitive
// configuration, and the CredentialBinding identity. Name, description, and
// enabled never change the digest; nor does any secret material (which never
// crosses this contract boundary).
func (configuration AuditExportConfiguration) DigestFor(exportType ExportType, credentialBindingID string) (string, error) {
	if err := configuration.ValidateFor(exportType); err != nil {
		return "", err
	}
	fields := append([]ConfigurationField(nil), configuration.Fields...)
	for left := range fields {
		for right := left + 1; right < len(fields); right++ {
			if fields[right].Name < fields[left].Name {
				fields[left], fields[right] = fields[right], fields[left]
			}
		}
	}
	payload, err := json.Marshal(struct {
		Type              ExportType
		Schema            ExportSchemaVersion
		CredentialBinding string
		Fields            []ConfigurationField
	}{exportType, configuration.SchemaVersion, credentialBindingID, fields})
	if err != nil {
		return "", fmt.Errorf("serialize normalized audit export configuration: %w", err)
	}
	sum := sha256.Sum256(payload)
	return fmt.Sprintf("sha256:%x", sum[:]), nil
}

// AuditExportTargetDraft contains only caller-controlled configuration. Stable
// target identity, normalized digest, revision, and enabled state are
// allocated/derived by the service and are returned in AuditExportTarget.
type AuditExportTargetDraft struct {
	Name                string                   `json:"name"`
	Description         string                   `json:"description"`
	ExportType          ExportType               `json:"export_type"`
	Configuration       AuditExportConfiguration `json:"configuration"`
	CredentialBindingID string                   `json:"credential_binding_id"`
}

func (draft AuditExportTargetDraft) MarshalJSON() ([]byte, error) {
	if err := draft.Validate(); err != nil {
		return nil, err
	}
	type auditExportTargetDraftWire AuditExportTargetDraft
	return json.Marshal(auditExportTargetDraftWire(draft))
}

func (draft AuditExportTargetDraft) Validate() error {
	if draft.Name == "" || draft.Description == "" || draft.ExportType == "" {
		return fmt.Errorf("audit export target draft name, description, and type are required")
	}
	if err := draft.Configuration.ValidateFor(draft.ExportType); err != nil {
		return err
	}
	if draft.ExportType.RequiresAuthentication() && draft.CredentialBindingID == "" {
		return fmt.Errorf("audit export type %s requires a credential binding", draft.ExportType)
	}
	if !draft.ExportType.RequiresAuthentication() && draft.CredentialBindingID != "" {
		return fmt.Errorf("audit export type %s does not permit a credential binding", draft.ExportType)
	}
	return nil
}

// ValidateWithPolicy binds the caller-owned draft to the authoritative scope
// and service-allocated target identity before resolving any credential.
// Scope is deliberately supplied by the management context, never by draft
// JSON, so a client cannot authorize a target in a different scope.
func (draft AuditExportTargetDraft) ValidateWithPolicy(policy ExportPolicy, allocatedTargetID, authoritativeScopeID string) error {
	if err := draft.Validate(); err != nil {
		return err
	}
	if allocatedTargetID == "" || authoritativeScopeID == "" {
		return fmt.Errorf("audit export target identity and authoritative scope are required before policy validation")
	}
	if draft.CredentialBindingID == "" {
		return nil
	}
	return validateAuthoritativeCredentialBinding(policy, draft.CredentialBindingID, authoritativeScopeID, draft.ExportType, allocatedTargetID)
}

// SaveAuditExportTargetResult is returned only after the service has allocated
// the stable identity and computed the immutable configuration digest.
type SaveAuditExportTargetResult struct {
	Target AuditExportTarget `json:"target"`
}

func (result SaveAuditExportTargetResult) MarshalJSON() ([]byte, error) {
	if err := result.Target.Validate(); err != nil {
		return nil, err
	}
	type saveAuditExportTargetResultWire SaveAuditExportTargetResult
	return json.Marshal(saveAuditExportTargetResultWire(result))
}

type CredentialBindingState string

const CredentialBindingActive CredentialBindingState = "active"

type CredentialBindingUsage string

const CredentialBindingUsageAuditExport CredentialBindingUsage = "audit-export"

// CredentialBinding identifies an existing platform credential without ever
// carrying authentication material in an export contract. The subject is the
// exact governed target (for example the audit export target identity).
type CredentialBinding struct {
	ID          string                   `json:"id"`
	ScopeID     string                   `json:"scope_id"`
	SubjectID   string                   `json:"subject_id"`
	State       CredentialBindingState   `json:"state"`
	Usages      []CredentialBindingUsage `json:"usages"`
	ExportTypes []ExportType             `json:"export_types"`
}

func (binding CredentialBinding) ValidateFor(scopeID string, exportType ExportType, subjectID string) error {
	if binding.ID == "" || binding.ScopeID != scopeID || binding.SubjectID != subjectID || binding.State != CredentialBindingActive || !containsBindingUsage(binding.Usages, CredentialBindingUsageAuditExport) || !containsExportType(binding.ExportTypes, exportType) {
		return fmt.Errorf("credential binding is not applicable to this audit export")
	}
	return nil
}

// CredentialBindingResolver is implemented by the credential authority. It
// prevents caller-supplied binding assertions from authorizing an export.
type CredentialBindingResolver interface {
	ResolveCredentialBinding(id string) (CredentialBinding, error)
}

func validateAuthoritativeCredentialBinding(policy ExportPolicy, bindingID, scopeID string, exportType ExportType, subjectID string) error {
	if policy.CredentialBindings == nil {
		return fmt.Errorf("audit export policy requires authoritative credential bindings")
	}
	binding, err := policy.CredentialBindings.ResolveCredentialBinding(bindingID)
	if err != nil {
		return err
	}
	if binding.ID != bindingID {
		return fmt.Errorf("resolved credential binding identity does not match the requested binding")
	}
	return binding.ValidateFor(scopeID, exportType, subjectID)
}

// TargetReplacementRequiredError is returned when a Save update attempts to
// mutate immutable target identity/configuration instead of using Replace.
type TargetReplacementRequiredError struct {
	Reason string
}

func (err TargetReplacementRequiredError) Error() string {
	if err.Reason == "" {
		return "target replacement is required for protected changes"
	}
	return "target replacement is required: " + err.Reason
}

// SaveAuditExportTargetRequest creates a target when ExpectedRevision is empty
// or updates the scope's committed target with an explicit CAS revision.
type SaveAuditExportTargetRequest struct {
	RequestContext   requestcontext.RequestContext `json:"request_context"`
	Draft            AuditExportTargetDraft        `json:"draft"`
	ExpectedRevision string                        `json:"expected_revision"`
}

func (request SaveAuditExportTargetRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil {
		return fmt.Errorf("save audit export requires a trusted request context: %w", err)
	}
	return request.Draft.Validate()
}

func (request SaveAuditExportTargetRequest) ValidateWithPolicy(policy ExportPolicy, allocatedTargetID string) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if request.ExpectedRevision != "" {
		return fmt.Errorf("save update requires a loaded committed target CAS validation")
	}
	return request.Draft.ValidateWithPolicy(policy, allocatedTargetID, request.RequestContext.ScopeID)
}

func (request SaveAuditExportTargetRequest) ValidateForTarget(target AuditExportTarget) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if request.ExpectedRevision == "" {
		return fmt.Errorf("save target update requires expected revision")
	}
	if target.ID == "" || target.ScopeID == "" || target.Revision == "" {
		return fmt.Errorf("save target update requires a complete persisted target identity and revision")
	}
	if target.ScopeID != request.RequestContext.ScopeID {
		return fmt.Errorf("save target update scope does not match the persisted target")
	}
	if request.ExpectedRevision != target.Revision {
		return fmt.Errorf("save target expected revision does not match the committed target revision")
	}
	if request.Draft.ExportType != target.ExportType || request.Draft.CredentialBindingID != target.CredentialBindingID {
		return TargetReplacementRequiredError{Reason: "export type or credential binding changed"}
	}
	configurationDigest, err := request.Draft.Configuration.DigestFor(request.Draft.ExportType, request.Draft.CredentialBindingID)
	if err != nil {
		return err
	}
	if configurationDigest != target.ConfigurationDigest {
		return TargetReplacementRequiredError{Reason: "configuration changed"}
	}
	return nil
}

func (request SaveAuditExportTargetRequest) ValidateForTargetWithPolicy(policy ExportPolicy, target AuditExportTarget) error {
	if err := request.ValidateForTarget(target); err != nil {
		return err
	}
	return request.Draft.ValidateWithPolicy(policy, target.ID, target.ScopeID)
}

// UpdateAuditExportTargetRequest only permits the mutable fields
// (name, description, enabled) and is CAS-bound by ExpectedRevision.
type UpdateAuditExportTargetRequest struct {
	RequestContext   requestcontext.RequestContext `json:"request_context"`
	TargetID         string                        `json:"target_id"`
	ExpectedRevision string                        `json:"expected_revision"`
	Name             string                        `json:"name"`
	Description      string                        `json:"description"`
}

func (request UpdateAuditExportTargetRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil || request.TargetID == "" || request.ExpectedRevision == "" || request.Name == "" || request.Description == "" {
		return fmt.Errorf("target update requires trusted request context, identity, CAS revision, name, and description")
	}
	return nil
}

func (request UpdateAuditExportTargetRequest) ValidateForTarget(target AuditExportTarget) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if target.ID != request.TargetID || target.ScopeID != request.RequestContext.ScopeID {
		return fmt.Errorf("target update context does not match the persisted target")
	}
	if request.ExpectedRevision != target.Revision {
		return fmt.Errorf("target update expected revision does not match the committed target revision")
	}
	return nil
}

// EnableAuditExportTargetRequest re-enables a target after the service-side
// authorization, schema, credential, and probe validation.
type EnableAuditExportTargetRequest struct {
	RequestContext      requestcontext.RequestContext `json:"request_context"`
	TargetID            string                        `json:"target_id"`
	ExpectedRevision    string                        `json:"expected_revision"`
	ConfigurationDigest string                        `json:"configuration_digest"`
}

func (request EnableAuditExportTargetRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil || request.TargetID == "" || request.ExpectedRevision == "" || request.ConfigurationDigest == "" {
		return fmt.Errorf("target enable requires trusted request context, identity, CAS revision, and configuration digest")
	}
	return nil
}

func (request EnableAuditExportTargetRequest) ValidateForTarget(target AuditExportTarget) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if target.ID != request.TargetID || target.ScopeID != request.RequestContext.ScopeID {
		return fmt.Errorf("target enable context does not match the persisted target")
	}
	if request.ExpectedRevision != target.Revision {
		return fmt.Errorf("target enable expected revision does not match the committed target revision")
	}
	if request.ConfigurationDigest != target.ConfigurationDigest {
		return fmt.Errorf("target enable configuration digest does not match the committed target")
	}
	return nil
}

// DisableAuditExportTargetRequest stops external delivery only; local events
// and the system checkpoint remain.
type DisableAuditExportTargetRequest struct {
	RequestContext      requestcontext.RequestContext `json:"request_context"`
	TargetID            string                        `json:"target_id"`
	ExpectedRevision    string                        `json:"expected_revision"`
	ConfigurationDigest string                        `json:"configuration_digest"`
}

func (request DisableAuditExportTargetRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil || request.TargetID == "" || request.ExpectedRevision == "" || request.ConfigurationDigest == "" {
		return fmt.Errorf("target disable requires trusted request context, identity, CAS revision, and configuration digest")
	}
	return nil
}

func (request DisableAuditExportTargetRequest) ValidateForTarget(target AuditExportTarget) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if target.ID != request.TargetID || target.ScopeID != request.RequestContext.ScopeID {
		return fmt.Errorf("target disable context does not match the persisted target")
	}
	if request.ExpectedRevision != target.Revision {
		return fmt.Errorf("target disable expected revision does not match the committed target revision")
	}
	if request.ConfigurationDigest != target.ConfigurationDigest {
		return fmt.Errorf("target disable configuration digest does not match the committed target")
	}
	return nil
}

// ReplaceAuditExportTargetRequest explicitly creates a new target
// (configuration, type, or binding change) and disables the current one; the
// replacement never inherits the old checkpoint.
type ReplaceAuditExportTargetRequest struct {
	RequestContext   requestcontext.RequestContext `json:"request_context"`
	CurrentTargetID  string                        `json:"current_target_id"`
	ExpectedRevision string                        `json:"expected_revision"`
	Replacement      AuditExportTargetDraft        `json:"replacement"`
}

func (request ReplaceAuditExportTargetRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil || request.CurrentTargetID == "" || request.ExpectedRevision == "" {
		return fmt.Errorf("target replacement requires trusted request context, current identity, and CAS revision")
	}
	return request.Replacement.Validate()
}

// ValidateWithPolicy receives the current target snapshot from the
// authoritative store and checks its identity, scope, and committed revision
// before any credential lookup. Replacements always receive a newly allocated
// ID and never inherit the current target's checkpoint.
func (request ReplaceAuditExportTargetRequest) ValidateWithPolicy(policy ExportPolicy, allocatedTargetID string, currentTarget AuditExportTarget) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if currentTarget.ID == "" || currentTarget.ScopeID == "" || currentTarget.Revision == "" || allocatedTargetID == "" {
		return fmt.Errorf("replacement identity and committed current target snapshot are required before policy validation")
	}
	if err := currentTarget.Validate(); err != nil {
		return fmt.Errorf("current target snapshot: %w", err)
	}
	if request.CurrentTargetID != currentTarget.ID {
		return fmt.Errorf("replacement current target identity does not match the committed target")
	}
	if request.ExpectedRevision != currentTarget.Revision {
		return fmt.Errorf("replacement expected revision does not match the committed target revision")
	}
	if request.RequestContext.ScopeID != currentTarget.ScopeID {
		return fmt.Errorf("replacement request context does not match the persisted current target scope")
	}
	if allocatedTargetID == request.CurrentTargetID {
		return fmt.Errorf("replacement must use a newly allocated target identity")
	}
	return request.Replacement.ValidateWithPolicy(policy, allocatedTargetID, currentTarget.ScopeID)
}

type AuditExportProbePayload struct {
	Summary    SafeSummary `json:"summary"`
	Redactions []Redaction `json:"redactions"`
}

func (payload AuditExportProbePayload) Validate() error {
	if err := payload.Summary.Validate(); err != nil || len(payload.Redactions) == 0 {
		return fmt.Errorf("audit export probe requires a bounded safe summary and redacted test payload")
	}
	for _, redaction := range payload.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("audit export probe redactions are invalid")
		}
	}
	return nil
}

func (payload AuditExportProbePayload) MarshalJSON() ([]byte, error) {
	if err := payload.Validate(); err != nil {
		return nil, err
	}
	type auditExportProbePayloadWire AuditExportProbePayload
	return json.Marshal(auditExportProbePayloadWire(payload))
}

type AuditExportProbeRequest struct {
	RequestContext requestcontext.RequestContext `json:"request_context"`
	Draft          AuditExportTargetDraft        `json:"draft"`
	Payload        AuditExportProbePayload       `json:"payload"`
	Timeout        time.Duration                 `json:"timeout"`
}

func (request AuditExportProbeRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil {
		return fmt.Errorf("audit export probe requires a trusted request context: %w", err)
	}
	if request.Timeout <= 0 {
		return fmt.Errorf("audit export probe requires an explicit timeout")
	}
	if err := request.Draft.Validate(); err != nil {
		return err
	}
	return request.Payload.Validate()
}

func (request AuditExportProbeRequest) MarshalJSON() ([]byte, error) {
	if err := request.Validate(); err != nil {
		return nil, err
	}
	type auditExportProbeRequestWire AuditExportProbeRequest
	return json.Marshal(auditExportProbeRequestWire(request))
}

func (request AuditExportProbeRequest) ValidateWithPolicy(policy ExportPolicy, allocatedTargetID string) error {
	if err := request.Validate(); err != nil {
		return err
	}
	if err := request.Draft.ValidateWithPolicy(policy, allocatedTargetID, request.RequestContext.ScopeID); err != nil {
		return err
	}
	return request.Payload.Validate()
}

type AuditExportProbeOutcome string

const (
	AuditExportProbeSucceeded AuditExportProbeOutcome = "succeeded"
	AuditExportProbeFailed    AuditExportProbeOutcome = "failed"
)

type AuditExportProbeResult struct {
	Outcome     AuditExportProbeOutcome `json:"outcome"`
	FailureCode ProbeFailureCode        `json:"failure_code"`
	Redactions  []Redaction             `json:"redactions"`
}

type ProbeFailureCode string

const (
	ProbeFailureTimeout     ProbeFailureCode = "timeout"
	ProbeFailureRejected    ProbeFailureCode = "rejected"
	ProbeFailureUnavailable ProbeFailureCode = "unavailable"
)

func (result AuditExportProbeResult) MarshalJSON() ([]byte, error) {
	if err := result.Validate(); err != nil {
		return nil, err
	}
	type auditExportProbeResultWire AuditExportProbeResult
	return json.Marshal(auditExportProbeResultWire(result))
}

func (result AuditExportProbeResult) Validate() error {
	if result.Outcome != AuditExportProbeSucceeded && result.Outcome != AuditExportProbeFailed {
		return fmt.Errorf("audit export probe result outcome is invalid")
	}
	if (result.Outcome == AuditExportProbeSucceeded && result.FailureCode != "") || (result.Outcome == AuditExportProbeFailed && !isProbeFailureCode(result.FailureCode)) {
		return fmt.Errorf("audit export probe result failure fields are inconsistent")
	}
	for _, redaction := range result.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("audit export probe result redactions are invalid")
		}
	}
	return nil
}

type AuditExportBatch struct {
	TargetID                  string             `json:"audit_export_target_id"`
	TargetScopeID             string             `json:"target_scope_id"`
	TargetConfigurationDigest string             `json:"target_configuration_digest"`
	DeliveryID                string             `json:"delivery_id"`
	StartCursor               string             `json:"start_cursor"`
	EndCursor                 string             `json:"end_cursor"`
	Events                    []AuditExportEvent `json:"events"`
	CursorInterval            []string           `json:"cursor_interval"`
}

// AuditExportEvent is the narrow, redacted projection permitted to leave the
// local audit authority. It intentionally excludes request context, controls,
// and change summaries.
type AuditExportEvent struct {
	ID            string      `json:"audit_event_id"`
	ScopeID       string      `json:"scope_id"`
	ProjectID     string      `json:"project_id"`
	PrincipalID   string      `json:"principal_id"`
	Subject       SubjectKey  `json:"subject"`
	Category      Category    `json:"category"`
	Action        string      `json:"action"`
	Outcome       Outcome     `json:"outcome"`
	OccurredAt    time.Time   `json:"occurred_at"`
	CausationID   string      `json:"causation_id"`
	CorrelationID string      `json:"correlation_id"`
	Cursor        string      `json:"cursor"`
	Redactions    []Redaction `json:"redactions"`
}

func (event AuditExportEvent) Validate() error {
	if event.ID == "" || event.ScopeID == "" || event.PrincipalID == "" || event.Subject.Kind == "" || event.Subject.ID == "" || event.Action == "" || event.Cursor == "" || !isCategory(event.Category) || !isOutcome(event.Outcome) || event.OccurredAt.IsZero() || event.CausationID == "" || event.CorrelationID == "" {
		return fmt.Errorf("audit export event requires redacted identity, subject, causal identity, type, and time")
	}
	for _, redaction := range event.Redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("audit export event redactions are invalid")
		}
	}
	return nil
}

func (event AuditExportEvent) MarshalJSON() ([]byte, error) {
	if err := event.Validate(); err != nil {
		return nil, err
	}
	event.OccurredAt = event.OccurredAt.UTC()
	type auditExportEventWire AuditExportEvent
	return json.Marshal(auditExportEventWire(event))
}

func (batch AuditExportBatch) MarshalJSON() ([]byte, error) {
	if err := batch.Validate(); err != nil {
		return nil, err
	}
	type auditExportBatchWire AuditExportBatch
	return json.Marshal(auditExportBatchWire(batch))
}

func (batch AuditExportBatch) Validate() error {
	if batch.TargetID == "" || batch.TargetScopeID == "" || batch.TargetConfigurationDigest == "" || batch.DeliveryID == "" || batch.StartCursor == "" || batch.EndCursor == "" || len(batch.Events) == 0 || len(batch.CursorInterval) != len(batch.Events) {
		return fmt.Errorf("audit export batch identity, target scope, cursors, and events are required")
	}
	seenIDs := make(map[string]struct{}, len(batch.Events))
	seenCursors := make(map[string]struct{}, len(batch.Events))
	for index, event := range batch.Events {
		if err := event.Validate(); err != nil {
			return err
		}
		if _, duplicate := seenIDs[event.ID]; duplicate {
			return fmt.Errorf("audit export batch cannot duplicate event IDs")
		}
		seenIDs[event.ID] = struct{}{}
		if _, duplicate := seenCursors[event.Cursor]; duplicate {
			return fmt.Errorf("audit export batch cannot duplicate cursors")
		}
		seenCursors[event.Cursor] = struct{}{}
		if event.ScopeID != batch.TargetScopeID {
			return fmt.Errorf("audit export event scope must match persisted target scope")
		}
		if index > 0 && !precedesExportEvent(batch.Events[index-1], event) {
			return fmt.Errorf("audit export events must use %s", AuditEventSortOrder)
		}
		if batch.CursorInterval[index] == "" || batch.CursorInterval[index] != event.Cursor {
			return fmt.Errorf("audit export batch cursor interval must bind every event")
		}
	}
	if batch.StartCursor != batch.CursorInterval[0] || batch.EndCursor != batch.CursorInterval[len(batch.CursorInterval)-1] {
		return fmt.Errorf("audit export batch cursors must bound the proven interval")
	}
	return nil
}

// ValidateForTarget binds a delivery batch to the persisted target snapshot
// selected by the service. Callers cannot substitute a target scope, identity,
// or configuration digest after the target has been loaded.
func (batch AuditExportBatch) ValidateForTarget(target AuditExportTarget) error {
	if err := target.Validate(); err != nil {
		return err
	}
	if err := batch.Validate(); err != nil {
		return err
	}
	if !target.Enabled {
		return fmt.Errorf("audit export target is disabled")
	}
	if batch.TargetID != target.ID || batch.TargetScopeID != target.ScopeID || batch.TargetConfigurationDigest != target.ConfigurationDigest {
		return fmt.Errorf("audit export batch is not bound to the persisted target")
	}
	return nil
}

var auditExportCSVHeader = []string{
	"audit_event_id", "scope_id", "project_id", "principal_id", "subject_kind", "subject_id", "subject_digest",
	"category", "action", "outcome", "occurred_at", "causation_id", "correlation_id", "cursor", "redactions",
}

// CanonicalAuditExportPayload deterministically serializes the validated
// redacted event sequence. Callers provide events, never arbitrary payload
// bytes; stream validation compares each supplied payload with this output.
func CanonicalAuditExportPayload(format ExportFormat, events []AuditExportEvent) ([]byte, error) {
	for _, event := range events {
		if err := event.Validate(); err != nil {
			return nil, err
		}
	}
	if format == ExportFormatJSONL {
		var payload bytes.Buffer
		for _, event := range events {
			event.OccurredAt = event.OccurredAt.UTC()
			encoded, err := json.Marshal(event)
			if err != nil {
				return nil, err
			}
			payload.Write(encoded)
			payload.WriteByte('\n')
		}
		return payload.Bytes(), nil
	}
	if format != ExportFormatCSV {
		return nil, fmt.Errorf("audit export format must be csv or jsonl")
	}
	var payload bytes.Buffer
	writer := csv.NewWriter(&payload)
	if err := writer.Write(auditExportCSVHeader); err != nil {
		return nil, err
	}
	for _, event := range events {
		redactions, err := json.Marshal(event.Redactions)
		if err != nil {
			return nil, err
		}
		if err := writer.Write([]string{
			event.ID, event.ScopeID, event.ProjectID, event.PrincipalID, event.Subject.Kind, event.Subject.ID, event.Subject.Digest,
			string(event.Category), event.Action, string(event.Outcome), event.OccurredAt.UTC().Format(time.RFC3339Nano), event.CausationID,
			event.CorrelationID, event.Cursor, string(redactions),
		}); err != nil {
			return nil, err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		return nil, err
	}
	return payload.Bytes(), nil
}

type AuditExportStream struct {
	Format ExportFormat `json:"format"`
	// Chunks are independently framed payloads. A CSV consumer must process
	// each chunk as a frame rather than concatenate payload bytes (each frame
	// carries its own canonical header).
	Chunks        []AuditExportChunk   `json:"chunks"`
	LimitExceeded *ExportLimitExceeded `json:"limit_exceeded"`
}

type AuditExportChunk struct {
	Payload []byte             `json:"payload"`
	Events  []AuditExportEvent `json:"events"`
}

func (chunk AuditExportChunk) CanonicalPayload(format ExportFormat) ([]byte, error) {
	return CanonicalAuditExportPayload(format, chunk.Events)
}

func (stream AuditExportStream) ValidateWithPolicy(policy ExportPolicy) error {
	if policy.MaxRows <= 0 || policy.MaxBytes <= 0 || (stream.Format != ExportFormatCSV && stream.Format != ExportFormatJSONL) {
		return fmt.Errorf("audit export stream must use a valid bounded policy and format")
	}
	if stream.LimitExceeded != nil {
		if len(stream.Chunks) != 0 {
			return fmt.Errorf("audit export limit result cannot contain successful chunks")
		}
		if err := stream.LimitExceeded.Validate(); err != nil {
			return err
		}
		if stream.LimitExceeded.Limit == ExportLimitRows && stream.LimitExceeded.Maximum > policy.MaxRows {
			return fmt.Errorf("reported row limit exceeds the active export policy")
		}
		if stream.LimitExceeded.Limit == ExportLimitBytes && stream.LimitExceeded.Maximum > policy.MaxBytes {
			return fmt.Errorf("reported byte limit exceeds the active export policy")
		}
		return nil
	}
	if len(stream.Chunks) == 0 {
		return fmt.Errorf("audit export stream requires chunks or a typed limit result")
	}
	rows, payloadBytes := 0, 0
	seenIDs := make(map[string]struct{})
	seenCursors := make(map[string]struct{})
	var previous *AuditExportEvent
	for _, chunk := range stream.Chunks {
		expectedPayload, err := chunk.CanonicalPayload(stream.Format)
		if err != nil {
			return err
		}
		if !bytes.Equal(chunk.Payload, expectedPayload) {
			return fmt.Errorf("audit export chunk payload is not canonical for its redacted events")
		}
		if len(chunk.Events) == 0 {
			if len(stream.Chunks) != 1 {
				return fmt.Errorf("empty audit export chunk cannot be combined with event chunks")
			}
			payloadBytes += len(chunk.Payload)
			continue
		}
		if len(chunk.Payload) == 0 {
			return fmt.Errorf("audit export chunks require payload and events")
		}
		rows += len(chunk.Events)
		payloadBytes += len(chunk.Payload)
		for _, event := range chunk.Events {
			if err := event.Validate(); err != nil {
				return err
			}
			if _, duplicate := seenIDs[event.ID]; duplicate {
				return fmt.Errorf("audit export stream cannot duplicate event IDs")
			}
			if _, duplicate := seenCursors[event.Cursor]; duplicate {
				return fmt.Errorf("audit export stream cannot duplicate cursors")
			}
			if previous != nil && !precedesExportEvent(*previous, event) {
				return fmt.Errorf("audit export stream events must use %s", AuditEventSortOrder)
			}
			seenIDs[event.ID] = struct{}{}
			seenCursors[event.Cursor] = struct{}{}
			copy := event
			previous = &copy
		}
	}
	if rows > policy.MaxRows || payloadBytes > policy.MaxBytes {
		return fmt.Errorf("audit export stream exceeds server policy")
	}
	return nil
}

func (stream AuditExportStream) MarshalJSON() ([]byte, error) {
	if err := stream.ValidateWithPolicy(DefaultExportPolicy()); err != nil {
		return nil, err
	}
	type auditExportStreamWire AuditExportStream
	return json.Marshal(auditExportStreamWire(stream))
}

// ValidateForRequest binds every exported event to the trusted management
// context that authorized the export, rather than trusting a caller-provided
// scope on the stream itself.
func (stream AuditExportStream) ValidateForRequest(request ExportAuditEventsRequest, queryPolicy QueryPolicy, policy ExportPolicy) error {
	if err := request.ValidateWithPolicy(queryPolicy, policy); err != nil {
		return err
	}
	if stream.Format != request.Format {
		return fmt.Errorf("audit export stream format must match the request format")
	}
	if err := stream.ValidateWithPolicy(policy); err != nil {
		return err
	}
	for _, chunk := range stream.Chunks {
		for _, event := range chunk.Events {
			if event.ScopeID != request.RequestContext.ScopeID {
				return fmt.Errorf("audit export event scope must match trusted request context")
			}
			if !exportFilterAccepts(request.Filter, event) {
				return fmt.Errorf("audit export event is outside the trusted request filter")
			}
		}
	}
	return nil
}

type AuditExportAcknowledgement struct {
	TargetID                string `json:"audit_export_target_id"`
	DeliveryID              string `json:"delivery_id"`
	StartCursor             string `json:"start_cursor"`
	HighestContiguousCursor string `json:"highest_contiguous_cursor"`
}

func (acknowledgement AuditExportAcknowledgement) Validate() error {
	if acknowledgement.TargetID == "" || acknowledgement.DeliveryID == "" || acknowledgement.StartCursor == "" || acknowledgement.HighestContiguousCursor == "" {
		return fmt.Errorf("audit export acknowledgement requires target, delivery, start, and contiguous cursors")
	}
	return nil
}

func (acknowledgement AuditExportAcknowledgement) MarshalJSON() ([]byte, error) {
	if err := acknowledgement.Validate(); err != nil {
		return nil, err
	}
	type auditExportAcknowledgementWire AuditExportAcknowledgement
	return json.Marshal(auditExportAcknowledgementWire(acknowledgement))
}

func (acknowledgement AuditExportAcknowledgement) ValidateFor(batch AuditExportBatch) error {
	if err := acknowledgement.Validate(); err != nil {
		return err
	}
	if err := batch.Validate(); err != nil {
		return err
	}
	if acknowledgement.TargetID != batch.TargetID || acknowledgement.DeliveryID != batch.DeliveryID || acknowledgement.StartCursor != batch.StartCursor {
		return fmt.Errorf("audit export acknowledgement is not bound to the delivered batch")
	}
	for _, cursor := range batch.CursorInterval {
		if cursor == acknowledgement.HighestContiguousCursor {
			return nil
		}
	}
	return fmt.Errorf("audit export acknowledgement must name a contiguous cursor in the delivered interval")
}

func (acknowledgement AuditExportAcknowledgement) ValidateForTarget(target AuditExportTarget, batch AuditExportBatch) error {
	if err := batch.ValidateForTarget(target); err != nil {
		return err
	}
	return acknowledgement.ValidateFor(batch)
}

type AuditExportPort interface {
	Probe(context.Context, AuditExportProbeRequest) (AuditExportProbeResult, error)
	// DeliverBatch receives the persisted target snapshot and must validate the
	// batch against it before any bytes cross the export boundary.
	DeliverBatch(context.Context, AuditExportTarget, AuditExportBatch) (AuditExportAcknowledgement, error)
}

func exportFilterAccepts(filter AuditEventFilter, event AuditExportEvent) bool {
	if event.OccurredAt.Before(filter.TimeRange.Start) || !event.OccurredAt.Before(filter.TimeRange.End) {
		return false
	}
	if filter.ProjectID != "" && event.ProjectID != filter.ProjectID {
		return false
	}
	if filter.Category != "" && event.Category != filter.Category {
		return false
	}
	if filter.Action != "" && event.Action != filter.Action {
		return false
	}
	if filter.Outcome != "" && event.Outcome != filter.Outcome {
		return false
	}
	if filter.PrincipalID != "" && event.PrincipalID != filter.PrincipalID {
		return false
	}
	if filter.Subject != nil && (event.Subject.Kind != filter.Subject.Kind || event.Subject.ID != filter.Subject.ID || (filter.Subject.Digest != "" && event.Subject.Digest != filter.Subject.Digest)) {
		return false
	}
	return filter.CorrelationID == "" || event.CorrelationID == filter.CorrelationID
}

func precedesExportEvent(left, right AuditExportEvent) bool {
	if !left.OccurredAt.Equal(right.OccurredAt) {
		return left.OccurredAt.After(right.OccurredAt)
	}
	return left.ID > right.ID
}

func isCategory(category Category) bool {
	switch category {
	case CategoryAuthentication, CategoryAuthorization, CategoryAdmission, CategoryDecision, CategoryCredential, CategoryStateWrite:
		return true
	default:
		return false
	}
}

func isOutcome(outcome Outcome) bool {
	return outcome == OutcomeAllowed || outcome == OutcomeDenied || outcome == OutcomeSucceeded || outcome == OutcomeFailed
}

func isRedactionReason(reason RedactionReason) bool {
	return reason == RedactionSecret || reason == RedactionNotAuthorized || reason == RedactionRetention
}

func isAvailability(availability RelatedRecordAvailability) bool {
	return availability == AvailabilityAvailable || availability == AvailabilityUnavailable || availability == AvailabilityRedacted || availability == AvailabilityNotAuthorized
}

func isAdmissionCheckStatus(status AdmissionCheckStatus) bool {
	return status == AdmissionCheckPass || status == AdmissionCheckFail || status == AdmissionCheckUnknown
}

func validateCursor(cursor string, binding CursorBinding, codec CursorCodec) (*CursorPosition, error) {
	if cursor == "" {
		return nil, nil
	}
	if codec == nil {
		return nil, fmt.Errorf("server cursor codec is required for a resumed page")
	}
	position, err := codec.ValidateCursor(cursor, binding)
	if err != nil {
		return nil, err
	}
	if position.OccurredAt.IsZero() || position.RecordID == "" {
		return nil, fmt.Errorf("cursor codec must authenticate a position")
	}
	return &position, nil
}

func cursorBinding(scopeID string, filter AuditEventFilter) (CursorBinding, error) {
	fingerprint, err := filterFingerprint(filter)
	if err != nil {
		return CursorBinding{}, err
	}
	return CursorBinding{Version: CursorVersion, ScopeID: scopeID, FilterFingerprint: fingerprint}, nil
}

var marshalCursorFilter = json.Marshal

func filterFingerprint(filter AuditEventFilter) (string, error) {
	payload, err := marshalCursorFilter(struct {
		Start         string
		End           string
		ProjectID     string
		Category      Category
		Action        string
		Outcome       Outcome
		PrincipalID   string
		Subject       *SubjectKey
		CorrelationID string
	}{filter.TimeRange.Start.UTC().Format(time.RFC3339Nano), filter.TimeRange.End.UTC().Format(time.RFC3339Nano), filter.ProjectID, filter.Category, filter.Action, filter.Outcome, filter.PrincipalID, filter.Subject, filter.CorrelationID})
	if err != nil {
		return "", fmt.Errorf("serialize audit cursor filter: %w", err)
	}
	return string(payload), nil
}

type exportSchema struct {
	version        ExportSchemaVersion
	allowedFields  map[ConfigurationFieldName]struct{}
	endpointScheme string
	requiresAuth   bool
}

func exportSchemaForType(exportType ExportType) (exportSchema, bool) {
	switch exportType {
	case ExportTypeWebhook:
		return exportSchema{version: ExportSchemaWebhookV1, allowedFields: map[ConfigurationFieldName]struct{}{ConfigurationFieldEndpoint: {}, ConfigurationFieldTenant: {}}, endpointScheme: "https", requiresAuth: true}, true
	case ExportTypeSyslog:
		return exportSchema{version: ExportSchemaSyslogV1, allowedFields: map[ConfigurationFieldName]struct{}{ConfigurationFieldEndpoint: {}, ConfigurationFieldIndex: {}}, endpointScheme: "tls", requiresAuth: false}, true
	default:
		return exportSchema{}, false
	}
}

func exportSchemaForVersion(version ExportSchemaVersion) (exportSchema, bool) {
	for _, exportType := range []ExportType{ExportTypeWebhook, ExportTypeSyslog} {
		spec, ok := exportSchemaForType(exportType)
		if ok && spec.version == version {
			return spec, true
		}
	}
	return exportSchema{}, false
}

func validExportEndpoint(spec exportSchema, value string) bool {
	endpoint, err := url.ParseRequestURI(value)
	if err != nil || endpoint.Scheme != spec.endpointScheme || endpoint.Host == "" || endpoint.User != nil {
		return false
	}
	return endpoint.RawQuery == ""
}

func containsBindingUsage(usages []CredentialBindingUsage, expected CredentialBindingUsage) bool {
	for _, usage := range usages {
		if usage == expected {
			return true
		}
	}
	return false
}
func containsExportType(exportTypes []ExportType, expected ExportType) bool {
	for _, exportType := range exportTypes {
		if exportType == expected {
			return true
		}
	}
	return false
}
func validateRedactions(redactions []Redaction) error {
	for _, redaction := range redactions {
		if redaction.Field == "" || !isRedactionReason(redaction.Reason) {
			return fmt.Errorf("redactions are invalid")
		}
	}
	return nil
}

func isExactPackageRelease(value string) bool {
	parts := strings.Split(value, "@")
	return len(parts) == 3 && parts[0] != "" && isStrictSemVer(parts[1]) && isSHA256Digest(parts[2])
}

func isStrictSemVer(value string) bool {
	if value == "" || strings.HasPrefix(value, "v") || strings.ContainsAny(value, " <>=~^*|") {
		return false
	}
	parts := strings.SplitN(value, "+", 2)
	coreAndPre := strings.SplitN(parts[0], "-", 2)
	core := strings.Split(coreAndPre[0], ".")
	if len(core) != 3 {
		return false
	}
	for _, part := range core {
		if !validSemVerCorePart(part) {
			return false
		}
	}
	if len(coreAndPre) == 2 && !validSemVerIdentifiers(coreAndPre[1], true, true) {
		return false
	}
	return len(parts) != 2 || validSemVerIdentifiers(parts[1], false, false)
}

func validSemVerCorePart(part string) bool {
	if part == "" || (len(part) > 1 && part[0] == '0') {
		return false
	}
	for _, character := range part {
		if character < '0' || character > '9' {
			return false
		}
	}
	return true
}

func validSemVerIdentifiers(value string, rejectNumericLeadingZero, rejectHyphenBoundary bool) bool {
	if value == "" {
		return false
	}
	for _, identifier := range strings.Split(value, ".") {
		if !validSemVerIdentifier(identifier, rejectNumericLeadingZero, rejectHyphenBoundary) {
			return false
		}
	}
	return true
}

func validSemVerIdentifier(identifier string, rejectNumericLeadingZero, rejectHyphenBoundary bool) bool {
	if identifier == "" || (rejectHyphenBoundary && (identifier[0] == '-' || identifier[len(identifier)-1] == '-')) {
		return false
	}
	digits := true
	for _, character := range identifier {
		if !(character >= '0' && character <= '9') && !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') && character != '-' {
			return false
		}
		if character < '0' || character > '9' {
			digits = false
		}
	}
	return !(rejectNumericLeadingZero && digits && len(identifier) > 1 && identifier[0] == '0')
}

func isSHA256Digest(value string) bool {
	if !strings.HasPrefix(value, "sha256:") || len(value) != len("sha256:")+64 {
		return false
	}
	for _, character := range strings.TrimPrefix(value, "sha256:") {
		if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')) {
			return false
		}
	}
	return true
}

func isProbeFailureCode(code ProbeFailureCode) bool {
	return code == ProbeFailureTimeout || code == ProbeFailureRejected || code == ProbeFailureUnavailable
}

func containsSensitiveMaterial(value string) bool {
	lower := strings.ToLower(value)
	return strings.Contains(lower, "secret") || strings.Contains(lower, "token") || strings.Contains(lower, "authorization") || strings.Contains(lower, "bearer") || strings.Contains(lower, "password")
}

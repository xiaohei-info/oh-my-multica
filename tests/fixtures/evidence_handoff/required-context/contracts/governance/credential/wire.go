package credential

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/governance/authz"
)

type CredentialBindingID string
type ScopeID string
type ProjectID string
type PrincipalID string
type CredentialType string
type CredentialUsage string

const (
	CredentialActionResolve  = "credential.resolve"
	CredentialSubjectBinding = "credential-binding"
)

type SubjectConstraint struct {
	Kind     string `json:"kind"`
	ID       string `json:"id"`
	Digest   string `json:"digest,omitempty"`
	Revision uint64 `json:"revision,omitempty"`
}

func (subject SubjectConstraint) Validate() error {
	if subject.Kind == "" || subject.ID == "" {
		return errors.New("credential subject kind and ID are required")
	}
	if subject.Revision != 0 {
		return errors.New("credential subject revision is not part of the wire identity")
	}
	return nil
}

type CredentialDisplay struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

type CredentialStatus string

const (
	CredentialStatusActive   CredentialStatus = "active"
	CredentialStatusDisabled CredentialStatus = "disabled"
	CredentialStatusRevoked  CredentialStatus = "revoked"
	CredentialStatusExpired  CredentialStatus = "expired"
)

func (status CredentialStatus) Validate() error {
	switch status {
	case CredentialStatusActive, CredentialStatusDisabled, CredentialStatusRevoked, CredentialStatusExpired:
		return nil
	default:
		return fmt.Errorf("credential status %q is invalid", status)
	}
}

// ValidateCredentialStatusTransition is the pure binding lifecycle table.
// Terminal revoked state cannot be reactivated by a copied wire value.
func ValidateCredentialStatusTransition(previous, next CredentialStatus) error {
	if err := previous.Validate(); err != nil {
		return err
	}
	if err := next.Validate(); err != nil {
		return err
	}
	valid := (previous == CredentialStatusActive && (next == CredentialStatusDisabled || next == CredentialStatusRevoked || next == CredentialStatusExpired)) ||
		(previous == CredentialStatusDisabled && (next == CredentialStatusRevoked || next == CredentialStatusExpired)) ||
		(previous == CredentialStatusExpired && next == CredentialStatusRevoked)
	if !valid {
		return fmt.Errorf("credential status transition %q -> %q is invalid", previous, next)
	}
	return nil
}

type TypeMetadata []byte

type TypeSpecificReauthorization struct {
	Kind      string       `json:"kind"`
	Reference string       `json:"reference"`
	Metadata  TypeMetadata `json:"metadata,omitempty"`
}

// SecretInput is write-only. Its bytes are deliberately unexported and cannot
// be reconstructed from a published wire value.
type SecretInput struct{ material []byte }

func NewSecretInput(material []byte) *SecretInput {
	return &SecretInput{material: append([]byte(nil), material...)}
}

func (input *SecretInput) IsEmpty() bool {
	return input == nil || len(input.material) == 0
}

func (input *SecretInput) Clear() {
	if input == nil {
		return
	}
	for index := range input.material {
		input.material[index] = 0
	}
	input.material = nil
}

type CredentialMaterialInput struct {
	Secret          *SecretInput                 `json:"secret,omitempty"`
	Reauthorization *TypeSpecificReauthorization `json:"reauthorization,omitempty"`
}

func (input CredentialMaterialInput) Validate() error {
	if (input.Secret == nil) == (input.Reauthorization == nil) {
		return errors.New("provide exactly one credential secret input or type-specific reauthorization")
	}
	if input.Secret != nil {
		if input.Secret.IsEmpty() {
			return errors.New("credential secret input is empty")
		}
		return nil
	}
	if input.Reauthorization.Kind == "" || input.Reauthorization.Reference == "" {
		return errors.New("credential reauthorization kind and reference are required")
	}
	return nil
}

type CredentialBindingInput struct {
	Display        CredentialDisplay       `json:"display"`
	CredentialType CredentialType          `json:"credential_type"`
	Material       CredentialMaterialInput `json:"material"`
	TypeMetadata   TypeMetadata            `json:"type_metadata,omitempty"`
	ExpiresAt      time.Time               `json:"expires_at,omitempty"`
}

func (input CredentialBindingInput) Validate() error {
	if input.Display.Name == "" || input.Display.Description == "" {
		return errors.New("credential display name and description are required")
	}
	if input.CredentialType == "" {
		return errors.New("credential type is required")
	}
	return input.Material.Validate()
}

type CredentialValidationResult struct {
	Succeeded bool      `json:"succeeded"`
	Code      string    `json:"code,omitempty"`
	Summary   string    `json:"summary,omitempty"`
	CheckedAt time.Time `json:"checked_at,omitempty"`
}

func (result CredentialValidationResult) Validate() error {
	if result.Code != "" && strings.TrimSpace(result.Code) != result.Code {
		return errors.New("credential validation code is not normalized")
	}
	if result.Summary != "" && strings.TrimSpace(result.Summary) != result.Summary {
		return errors.New("credential validation summary is not normalized")
	}
	if len(result.Summary) > 256 {
		return errors.New("credential validation summary is too long")
	}
	return nil
}

func (result CredentialValidationResult) validateSuccessful() error {
	if err := result.Validate(); err != nil {
		return err
	}
	if !result.Succeeded || result.CheckedAt.IsZero() || result.Code == "" || result.Summary == "" {
		return errors.New("credential validation evidence must be successful, redacted, and timestamped")
	}
	switch result.Code {
	case "ok", "validated":
	default:
		return errors.New("credential validation code is not provider-independent")
	}
	switch result.Summary {
	case "validation result available", "typed validation passed":
	default:
		return errors.New("credential validation summary is not provider-independent")
	}
	return nil
}

type CredentialBindingMetadata struct {
	CredentialBindingID  CredentialBindingID `json:"credential_binding_id"`
	ScopeID              ScopeID             `json:"scope_id"`
	ProjectID            ProjectID           `json:"project_id,omitempty"`
	Display              CredentialDisplay   `json:"display"`
	CredentialType       CredentialType      `json:"credential_type"`
	AllowedUsages        []CredentialUsage   `json:"allowed_usages"`
	SubjectConstraints   []SubjectConstraint `json:"subject_constraints,omitempty"`
	ExpiresAt            time.Time           `json:"expires_at,omitempty"`
	Fingerprint          string              `json:"fingerprint"`
	Status               CredentialStatus    `json:"status"`
	Revision             uint64              `json:"revision"`
	CreatedBy            PrincipalID         `json:"created_by"`
	CreatedAt            time.Time           `json:"created_at"`
	LastRotatedAt        time.Time           `json:"last_rotated_at,omitempty"`
	LastRotationRecordID string              `json:"last_rotation_record_id,omitempty"`
}

func (metadata CredentialBindingMetadata) Validate() error {
	if metadata.CredentialBindingID == "" || metadata.ScopeID == "" || metadata.Display.Name == "" || metadata.Display.Description == "" || metadata.CredentialType == "" || metadata.Fingerprint == "" || metadata.Revision == 0 || metadata.CreatedBy == "" || metadata.CreatedAt.IsZero() {
		return errors.New("credential binding metadata identity is incomplete")
	}
	if err := metadata.Status.Validate(); err != nil {
		return err
	}
	if len(metadata.AllowedUsages) == 0 {
		return errors.New("credential binding requires allowed usages")
	}
	seenUsages := make(map[CredentialUsage]struct{}, len(metadata.AllowedUsages))
	for _, usage := range metadata.AllowedUsages {
		if usage == "" {
			return errors.New("credential binding usage cannot be empty")
		}
		if _, exists := seenUsages[usage]; exists {
			return fmt.Errorf("credential binding usage %q is duplicated", usage)
		}
		seenUsages[usage] = struct{}{}
	}
	if metadata.LastRotatedAt.IsZero() != (metadata.LastRotationRecordID == "") {
		return errors.New("credential binding rotation evidence must include time and record ID together")
	}
	for _, subject := range metadata.SubjectConstraints {
		if err := subject.Validate(); err != nil {
			return err
		}
	}
	return nil
}

type CredentialRotationState string

const (
	CredentialRotationStatePending   CredentialRotationState = "pending"
	CredentialRotationStateUnknown   CredentialRotationState = "unknown"
	CredentialRotationStateCommitted CredentialRotationState = "committed"
	CredentialRotationStateAborted   CredentialRotationState = "aborted"
)

func (state CredentialRotationState) Validate() error {
	switch state {
	case CredentialRotationStatePending, CredentialRotationStateUnknown, CredentialRotationStateCommitted, CredentialRotationStateAborted:
		return nil
	default:
		return fmt.Errorf("credential rotation state %q is invalid", state)
	}
}

// CredentialRotationWire carries only state-independent identity and the pure
// revision invariant; durable CAS, authorization, and owner evidence remain
// outside this package.
type CredentialRotationWire struct {
	RotationID          string                  `json:"rotation_id"`
	CredentialBindingID CredentialBindingID     `json:"credential_binding_id"`
	ExpectedRevision    uint64                  `json:"expected_revision"`
	CurrentRevision     uint64                  `json:"current_revision"`
	State               CredentialRotationState `json:"state"`
	RecordDigest        string                  `json:"record_digest,omitempty"`
}

func (rotation CredentialRotationWire) Validate() error {
	if rotation.RotationID == "" || rotation.CredentialBindingID == "" || rotation.ExpectedRevision == 0 {
		return errors.New("credential rotation wire identity is incomplete")
	}
	if err := rotation.State.Validate(); err != nil {
		return err
	}
	switch rotation.State {
	case CredentialRotationStateCommitted:
		if rotation.CurrentRevision != rotation.ExpectedRevision+1 || rotation.RecordDigest == "" {
			return errors.New("committed credential rotation requires next revision and record digest")
		}
	case CredentialRotationStatePending, CredentialRotationStateUnknown, CredentialRotationStateAborted:
		if rotation.CurrentRevision != rotation.ExpectedRevision || rotation.RecordDigest != "" {
			return errors.New("uncommitted credential rotation has invalid revision or record evidence")
		}
	}
	return nil
}

// ValidateCredentialRotationTransition is the pure rotation state table.
func ValidateCredentialRotationTransition(previous, next CredentialRotationWire) error {
	if err := previous.Validate(); err != nil {
		return err
	}
	if err := next.Validate(); err != nil {
		return err
	}
	if previous.RotationID != next.RotationID || previous.CredentialBindingID != next.CredentialBindingID || previous.ExpectedRevision != next.ExpectedRevision {
		return errors.New("credential rotation transition identity conflicts")
	}
	valid := (previous.State == CredentialRotationStatePending && (next.State == CredentialRotationStateUnknown || next.State == CredentialRotationStateAborted)) ||
		(previous.State == CredentialRotationStateUnknown && (next.State == CredentialRotationStateCommitted || next.State == CredentialRotationStateAborted))
	if !valid {
		return fmt.Errorf("credential rotation transition %q -> %q is invalid", previous.State, next.State)
	}
	return nil
}

type CredentialRotationRecord struct {
	CredentialRotationRecordID string                     `json:"credential_rotation_record_id"`
	RotationID                 string                     `json:"rotation_id"`
	CredentialBindingID        CredentialBindingID        `json:"credential_binding_id"`
	PreviousRevision           uint64                     `json:"previous_revision"`
	CurrentRevision            uint64                     `json:"current_revision"`
	Fingerprint                string                     `json:"fingerprint"`
	ExpiresAt                  time.Time                  `json:"expires_at"`
	AuditEventID               string                     `json:"audit_event_id"`
	RecordDigest               string                     `json:"record_digest"`
	Validation                 CredentialValidationResult `json:"validation"`
	RotatedBy                  PrincipalID                `json:"rotated_by"`
	RotatedAt                  time.Time                  `json:"rotated_at"`
}

type credentialRotationRecordDigestProjection struct {
	CredentialRotationRecordID string                     `json:"credential_rotation_record_id"`
	RotationID                 string                     `json:"rotation_id"`
	CredentialBindingID        CredentialBindingID        `json:"credential_binding_id"`
	PreviousRevision           uint64                     `json:"previous_revision"`
	CurrentRevision            uint64                     `json:"current_revision"`
	Fingerprint                string                     `json:"fingerprint"`
	ExpiresAt                  time.Time                  `json:"expires_at"`
	AuditEventID               string                     `json:"audit_event_id"`
	Validation                 CredentialValidationResult `json:"validation"`
	RotatedBy                  PrincipalID                `json:"rotated_by"`
	RotatedAt                  time.Time                  `json:"rotated_at"`
}

func (record CredentialRotationRecord) validateShape() error {
	if record.CredentialRotationRecordID == "" || record.RotationID == "" || record.CredentialBindingID == "" || record.PreviousRevision == 0 || record.CurrentRevision != record.PreviousRevision+1 || record.Fingerprint == "" || record.ExpiresAt.IsZero() || record.AuditEventID == "" || record.RotatedBy == "" || record.RotatedAt.IsZero() {
		return errors.New("credential rotation record identity is incomplete")
	}
	return record.Validation.validateSuccessful()
}

func (record CredentialRotationRecord) digestProjection() credentialRotationRecordDigestProjection {
	return credentialRotationRecordDigestProjection{
		CredentialRotationRecordID: record.CredentialRotationRecordID,
		RotationID:                 record.RotationID,
		CredentialBindingID:        record.CredentialBindingID,
		PreviousRevision:           record.PreviousRevision,
		CurrentRevision:            record.CurrentRevision,
		Fingerprint:                record.Fingerprint,
		ExpiresAt:                  record.ExpiresAt,
		AuditEventID:               record.AuditEventID,
		Validation:                 record.Validation,
		RotatedBy:                  record.RotatedBy,
		RotatedAt:                  record.RotatedAt,
	}
}

func (record CredentialRotationRecord) Validate() error {
	if err := record.validateShape(); err != nil {
		return err
	}
	if record.RecordDigest == "" {
		return errors.New("credential rotation record digest is required")
	}
	expected, err := CredentialRotationRecordDigest(record)
	if err != nil {
		return err
	}
	if record.RecordDigest != expected {
		return errors.New("credential rotation record digest does not match canonical projection")
	}
	return nil
}

func CredentialRotationRecordDigest(record CredentialRotationRecord) (string, error) {
	if err := record.validateShape(); err != nil {
		return "", err
	}
	encoded, err := json.Marshal(record.digestProjection())
	if err != nil {
		return "", fmt.Errorf("marshal credential rotation record digest projection: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

type CredentialLeaseState string

const (
	CredentialLeaseActive   CredentialLeaseState = "active"
	CredentialLeaseConsumed CredentialLeaseState = "consumed"
	CredentialLeaseRevoked  CredentialLeaseState = "revoked"
	CredentialLeaseExpired  CredentialLeaseState = "expired"
)

func (state CredentialLeaseState) Validate() error {
	switch state {
	case CredentialLeaseActive, CredentialLeaseConsumed, CredentialLeaseRevoked, CredentialLeaseExpired:
		return nil
	default:
		return fmt.Errorf("credential lease state %q is invalid", state)
	}
}

func ValidateCredentialLeaseTransition(previous, next CredentialLeaseState) error {
	if err := previous.Validate(); err != nil {
		return err
	}
	if err := next.Validate(); err != nil {
		return err
	}
	valid := previous == CredentialLeaseActive && (next == CredentialLeaseConsumed || next == CredentialLeaseRevoked || next == CredentialLeaseExpired)
	if !valid {
		return fmt.Errorf("credential lease transition %q -> %q is invalid", previous, next)
	}
	return nil
}

type CredentialLeaseWire struct {
	LeaseID             string               `json:"lease_id"`
	CredentialBindingID CredentialBindingID  `json:"credential_binding_id"`
	Usage               CredentialUsage      `json:"usage"`
	Subject             SubjectConstraint    `json:"subject"`
	IssuedAt            time.Time            `json:"issued_at"`
	ExpiresAt           time.Time            `json:"expires_at"`
	State               CredentialLeaseState `json:"state"`
	RequestDigest       string               `json:"request_digest"`
}

func (lease CredentialLeaseWire) Validate() error {
	if lease.LeaseID == "" || lease.CredentialBindingID == "" || lease.Usage == "" || lease.IssuedAt.IsZero() || lease.ExpiresAt.IsZero() || lease.RequestDigest == "" {
		return errors.New("credential lease wire identity is incomplete")
	}
	if !lease.ExpiresAt.After(lease.IssuedAt) {
		return errors.New("credential lease expiry must follow issue time")
	}
	if err := lease.Subject.Validate(); err != nil {
		return err
	}
	return lease.State.Validate()
}

type CredentialMaterializationStatus string

const (
	CredentialMaterializationPending   CredentialMaterializationStatus = "pending"
	CredentialMaterializationSucceeded CredentialMaterializationStatus = "succeeded"
	CredentialMaterializationFailed    CredentialMaterializationStatus = "failed"
	CredentialMaterializationUnknown   CredentialMaterializationStatus = "unknown"
)

// CredentialMaterializationErrorCode is the redacted terminal error vocabulary.
type CredentialMaterializationErrorCode string

const (
	CredentialMaterializationErrorFailed        CredentialMaterializationErrorCode = "materialization_failed"
	CredentialMaterializationErrorUnknown       CredentialMaterializationErrorCode = "materialization_unknown"
	CredentialMaterializationErrorConsumerPanic CredentialMaterializationErrorCode = "materialization_consumer_panic"
	CredentialMaterializationErrorReplayFailed  CredentialMaterializationErrorCode = "materialization_replay_failed"
	CredentialMaterializationErrorReconcile     CredentialMaterializationErrorCode = "materialization_reconcile_failed"
)

func (code CredentialMaterializationErrorCode) Validate() error {
	switch code {
	case CredentialMaterializationErrorFailed, CredentialMaterializationErrorUnknown, CredentialMaterializationErrorConsumerPanic, CredentialMaterializationErrorReplayFailed, CredentialMaterializationErrorReconcile:
		return nil
	default:
		return fmt.Errorf("credential materialization error code %q is invalid", code)
	}
}

func (status CredentialMaterializationStatus) Validate() error {
	switch status {
	case CredentialMaterializationPending, CredentialMaterializationSucceeded, CredentialMaterializationFailed, CredentialMaterializationUnknown:
		return nil
	default:
		return fmt.Errorf("credential materialization status %q is invalid", status)
	}
}

type CredentialMaterializationResult struct {
	MaterializationID string                             `json:"materialization_id"`
	LeaseDigest       string                             `json:"lease_digest"`
	RequestDigest     string                             `json:"request_digest"`
	IdempotencyKey    string                             `json:"idempotency_key"`
	Status            CredentialMaterializationStatus    `json:"status"`
	ErrorCode         CredentialMaterializationErrorCode `json:"error_code,omitempty"`
	CompletedAt       time.Time                          `json:"completed_at,omitempty"`
}

func (result CredentialMaterializationResult) Validate() error {
	if result.MaterializationID == "" || result.LeaseDigest == "" || result.RequestDigest == "" || result.IdempotencyKey == "" {
		return errors.New("credential materialization result identity is required")
	}
	if err := result.Status.Validate(); err != nil {
		return err
	}
	switch result.Status {
	case CredentialMaterializationPending:
		if !result.CompletedAt.IsZero() || result.ErrorCode != "" {
			return errors.New("pending materialization has terminal fields")
		}
	case CredentialMaterializationSucceeded:
		if result.CompletedAt.IsZero() || result.ErrorCode != "" {
			return errors.New("successful materialization is incomplete")
		}
	case CredentialMaterializationFailed, CredentialMaterializationUnknown:
		if result.CompletedAt.IsZero() {
			return errors.New("terminal materialization requires completion time")
		}
		if err := result.ErrorCode.Validate(); err != nil {
			return err
		}
	}
	return nil
}

func ValidateCredentialMaterializationTransition(previous, next CredentialMaterializationResult) error {
	if err := previous.Validate(); err != nil {
		return err
	}
	if err := next.Validate(); err != nil {
		return err
	}
	if previous.MaterializationID != next.MaterializationID || previous.LeaseDigest != next.LeaseDigest || previous.RequestDigest != next.RequestDigest || previous.IdempotencyKey != next.IdempotencyKey {
		return errors.New("credential materialization identity conflicts")
	}
	switch previous.Status {
	case CredentialMaterializationPending:
		if next.Status == CredentialMaterializationPending {
			return errors.New("credential materialization remains pending")
		}
	case CredentialMaterializationUnknown:
		if next.Status != CredentialMaterializationUnknown && next.Status != CredentialMaterializationSucceeded && next.Status != CredentialMaterializationFailed {
			return errors.New("credential materialization transition is invalid")
		}
		if next.Status == CredentialMaterializationUnknown && next != previous {
			return errors.New("unknown credential materialization is immutable until reconciliation")
		}
	case CredentialMaterializationSucceeded, CredentialMaterializationFailed:
		if previous != next {
			return errors.New("terminal credential materialization is immutable")
		}
	default:
		return errors.New("credential materialization transition is invalid")
	}
	return nil
}

type MaterializationTargetKind string

const (
	MaterializationTargetConnector        MaterializationTargetKind = "connector"
	MaterializationTargetRuntime          MaterializationTargetKind = "runtime"
	MaterializationTargetDeploymentDriver MaterializationTargetKind = "deployment-driver"
)

func (kind MaterializationTargetKind) Validate() error {
	switch kind {
	case MaterializationTargetConnector, MaterializationTargetRuntime, MaterializationTargetDeploymentDriver:
		return nil
	default:
		return fmt.Errorf("materialization target kind %q is invalid", kind)
	}
}

type MaterializationTarget struct {
	Kind MaterializationTargetKind `json:"kind"`
	ID   string                    `json:"id"`
}

func (target MaterializationTarget) Validate() error {
	if err := target.Kind.Validate(); err != nil {
		return err
	}
	if target.ID == "" {
		return errors.New("materialization target ID is required")
	}
	return nil
}

type MaterializationBoundary struct {
	Target             MaterializationTarget `json:"target"`
	Usage              CredentialUsage       `json:"usage"`
	Subject            SubjectConstraint     `json:"subject"`
	DeploymentID       string                `json:"deployment_id,omitempty"`
	ReleaseID          string                `json:"release_id,omitempty"`
	TargetConfigDigest string                `json:"target_config_digest,omitempty"`
}

func (boundary MaterializationBoundary) Validate() error {
	if err := boundary.Target.Validate(); err != nil {
		return err
	}
	if boundary.Usage == "" {
		return errors.New("materialization boundary usage is required")
	}
	if err := boundary.Subject.Validate(); err != nil {
		return err
	}
	if boundary.Target.Kind == MaterializationTargetDeploymentDriver {
		if boundary.DeploymentID == "" || boundary.ReleaseID == "" || boundary.TargetConfigDigest == "" {
			return errors.New("deployment materialization boundary requires execution identity")
		}
		return nil
	}
	if boundary.DeploymentID != "" || boundary.ReleaseID != "" || boundary.TargetConfigDigest != "" {
		return errors.New("non-deployment materialization boundary carries deployment identity")
	}
	return nil
}

type CredentialResolveRequest struct {
	Authorization  authz.AuthorizationRequest `json:"authorization"`
	BindingID      CredentialBindingID        `json:"credential_binding_id"`
	Usage          CredentialUsage            `json:"usage"`
	Subject        SubjectConstraint          `json:"subject"`
	Target         MaterializationTarget      `json:"target"`
	IdempotencyKey string                     `json:"idempotency_key"`
}

func (request CredentialResolveRequest) Validate() error {
	if err := request.Authorization.Validate(); err != nil {
		return err
	}
	if request.BindingID == "" || request.Usage == "" || request.IdempotencyKey == "" {
		return errors.New("credential resolve request identity is incomplete")
	}
	if request.Authorization.ActionKey != CredentialActionResolve {
		return errors.New("credential resolve authorization action is not bound to resolve")
	}
	if request.Authorization.Subject.Kind != CredentialSubjectBinding || request.Authorization.Subject.ID != string(request.BindingID) {
		return errors.New("credential resolve authorization subject is not bound to the binding")
	}
	if err := request.Subject.Validate(); err != nil {
		return err
	}
	return request.Target.Validate()
}

func CredentialResolveRequestDigest(request CredentialResolveRequest) (string, error) {
	if err := request.Validate(); err != nil {
		return "", err
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return "", fmt.Errorf("marshal credential resolve request: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

// CredentialAuthorizationRequestDigest is the canonical digest used by the
// AuthorizationEngine and credential wire contracts. It binds every field in
// the validated request, including authentication and policy provenance.
func CredentialAuthorizationRequestDigest(request authz.AuthorizationRequest) (string, error) {
	if err := request.Validate(); err != nil {
		return "", err
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return "", fmt.Errorf("marshal credential authorization request: %w", err)
	}
	digest := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

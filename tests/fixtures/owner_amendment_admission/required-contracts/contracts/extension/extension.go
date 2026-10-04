// Package extension publishes the stable OAC Extension wire: exact Package
// identity and digest bindings, Harness Extension Describe values, scoped Run
// RequestContext, and immutable ComponentRun values. It owns contract shapes
// and fail-closed validators only; it never accepts caller-authored Package
// Governance or Harness Host production authority.
package extension

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

const sha256Prefix = "sha256:"

var (
	// These are the repository-wide canonical spellings used by the other
	// public contract packages. SemVer is deliberately strict: numeric
	// identifiers cannot contain leading zeroes and the version is exact.
	semanticVersionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$`)
	sha256DigestPattern    = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)
)

// PackageType is the fixed OAC package kind.
type PackageType string

const (
	PackageTypeSolution  PackageType = "solution"
	PackageTypeContent   PackageType = "content"
	PackageTypeExtension PackageType = "extension"
)

func (kind PackageType) valid() bool {
	switch kind {
	case PackageTypeSolution, PackageTypeContent, PackageTypeExtension:
		return true
	default:
		return false
	}
}

// InterfaceAPI is an exact public extension interface version.
type InterfaceAPI string

const (
	InterfaceAPIHarness        InterfaceAPI = "harness.oac.dev/v1"
	InterfaceAPIRuntime        InterfaceAPI = "runtime.oac.dev/v1"
	InterfaceAPIAuthentication InterfaceAPI = "authentication.oac.dev/v1"
	InterfaceAPIDeployment     InterfaceAPI = "deployment.oac.dev/v1"
)

func (api InterfaceAPI) valid() bool {
	switch api {
	case InterfaceAPIHarness, InterfaceAPIRuntime, InterfaceAPIAuthentication, InterfaceAPIDeployment:
		return true
	default:
		return false
	}
}

// Validate rejects an interface that has no corresponding public SDK.
func (api InterfaceAPI) Validate() error {
	if !api.valid() {
		return fmt.Errorf("unsupported interface api %q", api)
	}
	return nil
}

// Entrypoint is the ExtensionPackage startup entry. It is launched safely by
// the ExtensionManager and must never escape the package boundary.
type Entrypoint struct {
	Command string   `json:"command"`
	Args    []string `json:"args,omitempty"`
}

// Validate checks the package-relative command and arguments.
func (entrypoint Entrypoint) Validate() error {
	if err := required(entrypoint.Command, "entrypoint command"); err != nil {
		return err
	}
	if err := validateEntrypointPath(entrypoint.Command); err != nil {
		return err
	}
	for index, argument := range entrypoint.Args {
		if err := validateEntrypointPath(argument); err != nil {
			return fmt.Errorf("entrypoint argument %d: %w", index, err)
		}
	}
	return nil
}

// PackageEnvelope is the minimal author-declared envelope shared by solution,
// content and extension packages. It intentionally carries no artifact digest,
// trust tier or lifecycle policy: those are governance results assigned by the
// installer after verification, never self-declared by the package.
type PackageEnvelope struct {
	PackageType    PackageType  `json:"package_type"`
	PackageID      string       `json:"package_id"`
	PackageVersion string       `json:"package_version"`
	DescriptorPath string       `json:"descriptor_path,omitempty"`
	InterfaceAPI   InterfaceAPI `json:"interface_api,omitempty"`
	Entrypoint     *Entrypoint  `json:"entrypoint,omitempty"`
	Origin         string       `json:"origin"`
	PackageSource  string       `json:"package_source"`
	Signature      string       `json:"signature,omitempty"`
}

// Validate fails closed on an incomplete or self-claimed envelope.
func (envelope PackageEnvelope) Validate() error {
	if !envelope.PackageType.valid() {
		return fmt.Errorf("invalid package type %q", envelope.PackageType)
	}
	if err := required(envelope.PackageID, "package id"); err != nil {
		return err
	}
	if err := validateSemver(envelope.PackageVersion, "package version"); err != nil {
		return err
	}
	// Origin and package_source are provenance facts, not optional signature
	// decorations. Unsigned development packages still need an auditable source.
	if err := required(envelope.Origin, "origin"); err != nil {
		return err
	}
	if err := required(envelope.PackageSource, "package source"); err != nil {
		return err
	}
	if envelope.PackageType == PackageTypeExtension {
		if err := envelope.InterfaceAPI.Validate(); err != nil {
			return err
		}
		if envelope.Entrypoint == nil {
			return errors.New("extension package requires entrypoint")
		}
		if err := envelope.Entrypoint.Validate(); err != nil {
			return err
		}
	} else {
		if envelope.InterfaceAPI != "" {
			return errors.New("non-extension package must not declare interface api")
		}
		if envelope.Entrypoint != nil {
			return errors.New("non-extension package must not declare entrypoint")
		}
		if err := validatePackageRelativePath(envelope.DescriptorPath, "descriptor path"); err != nil {
			return err
		}
	}
	if envelope.PackageType == PackageTypeExtension && envelope.DescriptorPath != "" {
		if err := validatePackageRelativePath(envelope.DescriptorPath, "descriptor path"); err != nil {
			return err
		}
	}
	return nil
}

// PackageIdentity is the author-declared identity before installation computes
// the artifact digest. It is intentionally distinct from ExactPackageRef,
// which is the post-verification, artifact-pinned value used by bindings.
type PackageIdentity struct {
	PackageType    PackageType `json:"package_type"`
	PackageID      string      `json:"package_id"`
	PackageVersion string      `json:"package_version"`
}

// Validate confirms the declared identity is complete and well formed.
func (identity PackageIdentity) Validate() error {
	if !identity.PackageType.valid() {
		return fmt.Errorf("invalid package type %q", identity.PackageType)
	}
	if err := required(identity.PackageID, "package id"); err != nil {
		return err
	}
	return validateSemver(identity.PackageVersion, "package version")
}

// IdentityKey is the canonical pre-install identity string.
func (identity PackageIdentity) IdentityKey() string {
	return string(identity.PackageType) + "/" + identity.PackageID + "@" + identity.PackageVersion
}

// Identity returns the stable identity declared by the envelope. The verified
// artifact digest is intentionally unavailable until installation.
func (envelope PackageEnvelope) Identity() PackageIdentity {
	return PackageIdentity{
		PackageType:    envelope.PackageType,
		PackageID:      envelope.PackageID,
		PackageVersion: envelope.PackageVersion,
	}
}

// CanonicalDigest binds the author-declared envelope fields to a deterministic
// SHA-256 digest. The artifact digest verified at install is computed over the
// complete package artifact, not over this envelope alone.
func (envelope PackageEnvelope) CanonicalDigest() (string, error) {
	if err := envelope.Validate(); err != nil {
		return "", err
	}
	return canonicalSHA256(envelope)
}

// ExactPackageRef identifies one exact, artifact-pinned package release. It
// is the only identity accepted by binding, catalog and recovery resolution.
type ExactPackageRef struct {
	PackageType    PackageType `json:"package_type"`
	PackageID      string      `json:"package_id"`
	PackageVersion string      `json:"package_version"`
	ArtifactDigest string      `json:"artifact_digest"`
}

// ExactComponentRef is the terminology used by the shared design for a
// package-pinned value. The aliases keep extension consumers target-named
// without creating a second release identity implementation.
type ExactComponentRef = ExactPackageRef
type ExtensionPackageRef = ExactPackageRef

// Validate confirms the release identity is complete and well formed.
func (reference ExactPackageRef) Validate() error {
	if !reference.PackageType.valid() {
		return fmt.Errorf("invalid package type %q", reference.PackageType)
	}
	if err := required(reference.PackageID, "package id"); err != nil {
		return err
	}
	if err := validateSemver(reference.PackageVersion, "package version"); err != nil {
		return err
	}
	return validateDigest(reference.ArtifactDigest, "artifact digest")
}

// IdentityKey is the canonical stable identity string for the exact release.
func (reference ExactPackageRef) IdentityKey() string {
	key := string(reference.PackageType) + "/" + reference.PackageID + "@" + reference.PackageVersion
	if reference.ArtifactDigest != "" {
		key += "#" + reference.ArtifactDigest
	}
	return key
}

// CanonicalDigest binds all exact release identity fields.
func (reference ExactPackageRef) CanonicalDigest() (string, error) {
	if err := reference.Validate(); err != nil {
		return "", err
	}
	return canonicalSHA256(reference)
}

// ExecutionMode is the closed ExtensionPackage process boundary.
type ExecutionMode string

const (
	ExecutionModeService          ExecutionMode = "service"
	ExecutionModeTrustedInProcess ExecutionMode = "trusted-in-process"
)

func (mode ExecutionMode) valid() bool {
	return mode == ExecutionModeService || mode == ExecutionModeTrustedInProcess
}

// Validate rejects execution modes outside the public SDK contract.
func (mode ExecutionMode) Validate() error {
	if !mode.valid() {
		return fmt.Errorf("unsupported execution mode %q", mode)
	}
	return nil
}

// ExtensionPoint is a fixed OAC harness extension point.
type ExtensionPoint string

const (
	ExtensionPointGuideComputational  ExtensionPoint = "harness.guide.computational"
	ExtensionPointGuideInferential    ExtensionPoint = "harness.guide.inferential"
	ExtensionPointExecutor            ExtensionPoint = "executor"
	ExtensionPointSensorComputational ExtensionPoint = "harness.sensor.computational"
	ExtensionPointSensorInferential   ExtensionPoint = "harness.sensor.inferential"
	ExtensionPointTrigger             ExtensionPoint = "trigger"
	ExtensionPointEventHandler        ExtensionPoint = "event-handler"
)

func (point ExtensionPoint) valid() bool {
	switch point {
	case ExtensionPointGuideComputational, ExtensionPointGuideInferential,
		ExtensionPointExecutor, ExtensionPointSensorComputational,
		ExtensionPointSensorInferential, ExtensionPointTrigger, ExtensionPointEventHandler:
		return true
	default:
		return false
	}
}

// EffectMode is the declared side-effect semantics of an Extension.
type EffectMode string

const (
	EffectModePure          EffectMode = "pure"
	EffectModeReadOnly      EffectMode = "read-only"
	EffectModeSideEffecting EffectMode = "side-effecting"
)

func (mode EffectMode) valid() bool {
	switch mode {
	case EffectModePure, EffectModeReadOnly, EffectModeSideEffecting:
		return true
	default:
		return false
	}
}

// Display is the localized capability name and description shared by the
// editor, detail and run-tracking surfaces. It never participates in identity.
type Display struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

func (display Display) validate() error {
	if err := required(display.Name, "display name"); err != nil {
		return err
	}
	return required(display.Description, "display description")
}

// Permission is one minimal scoped Core Service permission.
type Permission string

const (
	PermissionWorkflowRead      Permission = "workflow.read"
	PermissionArtifactRead      Permission = "artifact.read"
	PermissionArtifactWrite     Permission = "artifact.write"
	PermissionEventRead         Permission = "event.read"
	PermissionObservationSubmit Permission = "observation.submit"
	PermissionAgentInvoke       Permission = "agent.invoke"
	PermissionWorkspaceUse      Permission = "workspace.use"
	PermissionCredentialUse     Permission = "credential.use"
	PermissionDecisionRequest   Permission = "decision.request"
)

var scopedPermissions = map[Permission]bool{
	PermissionWorkflowRead:      true,
	PermissionArtifactRead:      true,
	PermissionArtifactWrite:     true,
	PermissionEventRead:         true,
	PermissionObservationSubmit: true,
	PermissionAgentInvoke:       true,
	PermissionWorkspaceUse:      true,
	PermissionCredentialUse:     true,
	PermissionDecisionRequest:   true,
}

// Extension is one capability described by an ExtensionPackage through the SDK
// Describe() contract. Extension IDs are unique within one package.
type Extension struct {
	ExtensionID    string         `json:"extension_id"`
	Display        Display        `json:"display"`
	ExtensionPoint ExtensionPoint `json:"extension_point"`
	EffectMode     EffectMode     `json:"effect_mode"`
	Permissions    []Permission   `json:"permissions"`
	InputSchema    string         `json:"input_schema,omitempty"`
	OutputSchema   string         `json:"output_schema,omitempty"`
}

// Validate fails closed on unknown extension points, self-declared side
// effects for pure extension positions, and unknown or duplicate permissions.
func (extension Extension) Validate() error {
	if err := required(extension.ExtensionID, "extension id"); err != nil {
		return err
	}
	if err := extension.Display.validate(); err != nil {
		return err
	}
	if !extension.ExtensionPoint.valid() {
		return fmt.Errorf("unsupported extension point %q", extension.ExtensionPoint)
	}
	if !extension.EffectMode.valid() {
		return fmt.Errorf("invalid effect mode %q", extension.EffectMode)
	}
	if extension.isPurePosition() && extension.EffectMode == EffectModeSideEffecting {
		return fmt.Errorf("%s must not declare side-effecting effect mode", extension.ExtensionPoint)
	}
	if extension.Permissions == nil {
		return errors.New("permissions array is required")
	}
	seenPermissions := make(map[Permission]struct{}, len(extension.Permissions))
	for _, permission := range extension.Permissions {
		if !scopedPermissions[permission] {
			return fmt.Errorf("unknown permission %q", permission)
		}
		if _, seen := seenPermissions[permission]; seen {
			return fmt.Errorf("duplicate permission %q", permission)
		}
		seenPermissions[permission] = struct{}{}
	}
	if containsPermission(extension.Permissions, PermissionAgentInvoke) && extension.ExtensionPoint != ExtensionPointExecutor {
		return errors.New("agent.invoke is only declarable by an executor extension")
	}
	return nil
}

func (extension Extension) isPurePosition() bool {
	switch extension.ExtensionPoint {
	case ExtensionPointGuideComputational, ExtensionPointGuideInferential,
		ExtensionPointSensorComputational, ExtensionPointSensorInferential:
		return true
	default:
		return false
	}
}

// ExtensionPackage is the typed Harness SDK package-level Describe wire. The
// package envelope supplies identity/interface/entrypoint; this value supplies
// the closed process mode and the complete SDK-described capability list. It
// does not install, select, invoke, or persist the package.
type ExtensionPackage struct {
	Package       PackageEnvelope `json:"package"`
	ExecutionMode ExecutionMode   `json:"execution_mode"`
	Extensions    []Extension     `json:"extensions"`
}

// Validate checks the package-level Describe contract and capability identity.
func (extensionPackage ExtensionPackage) Validate() error {
	if err := extensionPackage.Package.Validate(); err != nil {
		return fmt.Errorf("invalid extension package envelope: %w", err)
	}
	if extensionPackage.Package.PackageType != PackageTypeExtension {
		return errors.New("extension package wire requires package type extension")
	}
	if extensionPackage.Package.InterfaceAPI != InterfaceAPIHarness {
		return errors.New("harness extension package wire requires harness interface api")
	}
	if err := extensionPackage.ExecutionMode.Validate(); err != nil {
		return err
	}
	if len(extensionPackage.Extensions) == 0 {
		return errors.New("extension package requires at least one described extension")
	}
	seen := make(map[string]struct{}, len(extensionPackage.Extensions))
	for index, capability := range extensionPackage.Extensions {
		if err := capability.Validate(); err != nil {
			return fmt.Errorf("extension %d: %w", index, err)
		}
		if _, exists := seen[capability.ExtensionID]; exists {
			return fmt.Errorf("duplicate extension id %q", capability.ExtensionID)
		}
		seen[capability.ExtensionID] = struct{}{}
	}
	return nil
}

// SubjectKey identifies the current subject bound to a run or binding.
type SubjectKey struct {
	Kind   string `json:"kind"`
	ID     string `json:"id"`
	Digest string `json:"digest"`
}

func (subject SubjectKey) validate() error {
	if err := required(subject.Kind, "subject kind"); err != nil {
		return err
	}
	if err := required(subject.ID, "subject id"); err != nil {
		return err
	}
	return validateDigest(subject.Digest, "subject digest")
}

// HarnessExtensionBinding fixes one Extension inside a HarnessDefinition.
// The same identity and digest fields must resolve to identical assembly
// semantics; configuration or implementation changes form new digests.
type HarnessExtensionBinding struct {
	BindingID        string              `json:"binding_id"`
	ExtensionPackage ExtensionPackageRef `json:"extension_package"`
	ArtifactDigest   string              `json:"artifact_digest"`
	ConfigDigest     string              `json:"config_digest"`
	ExtensionID      string              `json:"extension_id"`
	Required         bool                `json:"required"`
	Subject          SubjectKey          `json:"subject"`
}

// Validate fails closed on incomplete bindings, non-canonical digests, or
// contradictory duplicate package digest fields.
func (binding HarnessExtensionBinding) Validate() error {
	if err := required(binding.BindingID, "binding id"); err != nil {
		return err
	}
	if binding.ExtensionPackage.PackageType != PackageTypeExtension {
		return errors.New("harness binding requires extension package")
	}
	if err := binding.ExtensionPackage.Validate(); err != nil {
		return fmt.Errorf("invalid bound extension package: %w", err)
	}
	if err := validateDigest(binding.ArtifactDigest, "artifact digest"); err != nil {
		return err
	}
	if binding.ExtensionPackage.ArtifactDigest != binding.ArtifactDigest {
		return errors.New("bound extension package artifact digest does not match artifact digest")
	}
	if err := validateDigest(binding.ConfigDigest, "config digest"); err != nil {
		return err
	}
	if err := required(binding.ExtensionID, "extension id"); err != nil {
		return err
	}
	return binding.Subject.validate()
}

// BindingDigest binds every fixed binding field, including Required, to a
// canonical digest so identical assemblies always resolve identically.
func (binding HarnessExtensionBinding) BindingDigest() (string, error) {
	if err := binding.Validate(); err != nil {
		return "", err
	}
	canonical := struct {
		BindingID        string              `json:"binding_id"`
		ExtensionPackage ExtensionPackageRef `json:"extension_package"`
		ArtifactDigest   string              `json:"artifact_digest"`
		ConfigDigest     string              `json:"config_digest"`
		ExtensionID      string              `json:"extension_id"`
		Required         bool                `json:"required"`
		Subject          SubjectKey          `json:"subject"`
	}{
		BindingID:        binding.BindingID,
		ExtensionPackage: binding.ExtensionPackage,
		ArtifactDigest:   binding.ArtifactDigest,
		ConfigDigest:     binding.ConfigDigest,
		ExtensionID:      binding.ExtensionID,
		Required:         binding.Required,
		Subject:          binding.Subject,
	}
	return canonicalSHA256(canonical)
}

// GateEvaluatorRegistration is an administrator-installed trusted in-process
// pure Gate Evaluator. Identity and release digest come from the verified
// install; callers cannot self-declare evaluator authority.
type GateEvaluatorRegistration struct {
	Display          Display             `json:"display"`
	ExtensionPackage ExtensionPackageRef `json:"extension_package"`
	ReleaseDigest    string              `json:"release_digest"`
}

// Validate fails closed on a self-attested evaluator registration.
func (registration GateEvaluatorRegistration) Validate() error {
	if err := registration.Display.validate(); err != nil {
		return err
	}
	if registration.ExtensionPackage.PackageType != PackageTypeExtension {
		return errors.New("gate evaluator requires extension package")
	}
	if err := registration.ExtensionPackage.Validate(); err != nil {
		return fmt.Errorf("invalid evaluator extension package: %w", err)
	}
	if err := validateDigest(registration.ReleaseDigest, "release digest"); err != nil {
		return err
	}
	if registration.ExtensionPackage.ArtifactDigest != registration.ReleaseDigest {
		return errors.New("evaluator release digest does not match extension package artifact digest")
	}
	return nil
}

// RunRequestContext is the scoped context a Harness Host binds to one
// invocation: current Workflow, WorkUnit, iteration, ComponentRun and subject.
// It is host-derived; a caller-authored RequestContext is never authority.
type RunRequestContext struct {
	WorkflowID     string     `json:"workflow_id"`
	WorkUnitID     string     `json:"work_unit_id"`
	Iteration      int64      `json:"iteration"`
	ComponentRunID string     `json:"component_run_id,omitempty"`
	BindingID      string     `json:"binding_id"`
	Subject        SubjectKey `json:"subject"`
	RequestDigest  string     `json:"request_digest"`
}

// RequestContext is the shorter public name used by extension callers.
type RequestContext = RunRequestContext

// ValidateStructure checks the transport shape and canonical digest only. It
// is deliberately separate from host authority: any caller can reproduce a
// digest, so structural validity is not an authorization proof.
func (context RunRequestContext) ValidateStructure() error {
	if err := required(context.WorkflowID, "workflow id"); err != nil {
		return err
	}
	if err := required(context.WorkUnitID, "work unit id"); err != nil {
		return err
	}
	if context.Iteration < 0 {
		return errors.New("iteration must not be negative")
	}
	if err := required(context.BindingID, "binding id"); err != nil {
		return err
	}
	if err := context.Subject.validate(); err != nil {
		return err
	}
	if err := validateDigest(context.RequestDigest, "request digest"); err != nil {
		return err
	}
	canonical, err := context.CanonicalDigest()
	if err != nil {
		return err
	}
	if context.RequestDigest != canonical {
		return errors.New("request digest does not match canonical scoped context")
	}
	return nil
}

// ErrHostProvenanceRequired makes the authority boundary explicit. A public
// wire value has no host-issued provenance token; Harness Host must apply its
// own authenticated binding before treating the context as authoritative.
var ErrHostProvenanceRequired = errors.New("request context requires host provenance")

// Validate never promotes a caller-computed digest to host authority. It still
// reports malformed structure first, then fails closed at the authority edge.
func (context RunRequestContext) Validate() error {
	if err := context.ValidateStructure(); err != nil {
		return err
	}
	return ErrHostProvenanceRequired
}

// CanonicalDigest deterministically binds the scoped context fields.
func (context RunRequestContext) CanonicalDigest() (string, error) {
	canonical := struct {
		WorkflowID     string     `json:"workflow_id"`
		WorkUnitID     string     `json:"work_unit_id"`
		Iteration      int64      `json:"iteration"`
		ComponentRunID string     `json:"component_run_id,omitempty"`
		BindingID      string     `json:"binding_id"`
		Subject        SubjectKey `json:"subject"`
	}{
		WorkflowID:     context.WorkflowID,
		WorkUnitID:     context.WorkUnitID,
		Iteration:      context.Iteration,
		ComponentRunID: context.ComponentRunID,
		BindingID:      context.BindingID,
		Subject:        context.Subject,
	}
	return canonicalSHA256(canonical)
}

// ComponentRunStatus is a single-meaning status value.
type ComponentRunStatus string

const (
	ComponentRunPending    ComponentRunStatus = "Pending"
	ComponentRunRunning    ComponentRunStatus = "Running"
	ComponentRunSucceeded  ComponentRunStatus = "Succeeded"
	ComponentRunFailed     ComponentRunStatus = "Failed"
	ComponentRunUnknown    ComponentRunStatus = "Unknown"
	ComponentRunCancelling ComponentRunStatus = "Cancelling"
	ComponentRunCancelled  ComponentRunStatus = "Cancelled"
)

func (status ComponentRunStatus) valid() bool {
	switch status {
	case ComponentRunPending, ComponentRunRunning, ComponentRunSucceeded,
		ComponentRunFailed, ComponentRunUnknown, ComponentRunCancelling, ComponentRunCancelled:
		return true
	default:
		return false
	}
}

// ExternalOperation is the tagged union identifying the external side effect
// of a ComponentRun, used for Observe and recovery.
type ExternalOperation struct {
	SystemType string `json:"system_type"`
	ExternalID string `json:"external_id"`
	URI        string `json:"uri,omitempty"`
}

// Validate confirms an external operation can be observed.
func (operation ExternalOperation) Validate() error {
	if err := required(operation.SystemType, "external operation system type"); err != nil {
		return err
	}
	return required(operation.ExternalID, "external operation id")
}

// ComponentRunResultKind is the tagged result union of a ComponentRun.
type ComponentRunResultKind string

const (
	ResultArtifact    ComponentRunResultKind = "artifact"
	ResultObservation ComponentRunResultKind = "observation"
	ResultReport      ComponentRunResultKind = "report"
	ResultExternal    ComponentRunResultKind = "external"
)

// ComponentRunResult is a structured result that is persisted before it is
// consumed. Exactly one kind-carrying field is set.
type ComponentRunResult struct {
	Kind          ComponentRunResultKind `json:"kind"`
	ArtifactID    string                 `json:"artifact_id,omitempty"`
	ObservationID string                 `json:"observation_id,omitempty"`
	ReportID      string                 `json:"report_id,omitempty"`
	ExternalID    string                 `json:"external_id,omitempty"`
	Digest        string                 `json:"digest"`
}

// Validate exhaustively checks the tagged union, rejecting unrelated payload
// identities rather than silently accepting contradictory wire values.
func (result ComponentRunResult) Validate() error {
	if err := validateDigest(result.Digest, "result digest"); err != nil {
		return err
	}
	switch result.Kind {
	case ResultArtifact:
		if result.ArtifactID == "" {
			return errors.New("artifact result requires artifact id")
		}
		if result.ObservationID != "" || result.ReportID != "" || result.ExternalID != "" {
			return errors.New("artifact result must not contain unrelated payload ids")
		}
	case ResultObservation:
		if result.ObservationID == "" {
			return errors.New("observation result requires observation id")
		}
		if result.ArtifactID != "" || result.ReportID != "" || result.ExternalID != "" {
			return errors.New("observation result must not contain unrelated payload ids")
		}
	case ResultReport:
		if result.ReportID == "" {
			return errors.New("report result requires report id")
		}
		if result.ArtifactID != "" || result.ObservationID != "" || result.ExternalID != "" {
			return errors.New("report result must not contain unrelated payload ids")
		}
	case ResultExternal:
		if result.ExternalID == "" {
			return errors.New("external result requires external id")
		}
		if result.ArtifactID != "" || result.ObservationID != "" || result.ReportID != "" {
			return errors.New("external result must not contain unrelated payload ids")
		}
	default:
		return fmt.Errorf("invalid result kind %q", result.Kind)
	}
	return nil
}

// ComponentRunError records a structured failure with explicit retry and
// outcome knowledge so recovery never guesses.
type ComponentRunError struct {
	Class        string `json:"class"`
	Retryable    bool   `json:"retryable"`
	OutcomeKnown bool   `json:"outcome_known"`
	Message      string `json:"message"`
}

// Validate checks the structured error fields. Retryability is descriptive
// only; this wire never carries proof that can authorize a repeat side effect.
func (failure ComponentRunError) Validate() error {
	return required(failure.Class, "error class")
}

// ComponentRun is the immutable invocation record for calls that need
// independent idempotency, provenance, external results, errors or recovery.
type ComponentRun struct {
	ComponentRunID    string              `json:"component_run_id"`
	ExtensionPackage  ExactPackageRef     `json:"extension_package"`
	ArtifactDigest    string              `json:"artifact_digest"`
	WorkflowID        string              `json:"workflow_id"`
	WorkUnitID        string              `json:"work_unit_id"`
	Iteration         int64               `json:"iteration"`
	Attempt           int64               `json:"attempt"`
	ExtensionID       string              `json:"extension_id"`
	HarnessBindingID  string              `json:"harness_binding_id"`
	Subject           SubjectKey          `json:"subject"`
	IdempotencyKey    string              `json:"idempotency_key"`
	RequestDigest     string              `json:"request_digest"`
	Status            ComponentRunStatus  `json:"status"`
	ExternalOperation *ExternalOperation  `json:"external_operation,omitempty"`
	Result            *ComponentRunResult `json:"result,omitempty"`
	Error             *ComponentRunError  `json:"error,omitempty"`
}

// Validate fails closed on incomplete runs, non-canonical identity, and
// contradictory status/payload combinations.
func (run ComponentRun) Validate() error {
	if err := required(run.ComponentRunID, "component run id"); err != nil {
		return err
	}
	if run.ExtensionPackage.PackageType != PackageTypeExtension {
		return errors.New("component run requires extension package")
	}
	if err := run.ExtensionPackage.Validate(); err != nil {
		return fmt.Errorf("invalid run extension package: %w", err)
	}
	if err := validateDigest(run.ArtifactDigest, "artifact digest"); err != nil {
		return err
	}
	if run.ExtensionPackage.ArtifactDigest != run.ArtifactDigest {
		return errors.New("run extension package artifact digest does not match artifact digest")
	}
	if err := required(run.WorkflowID, "workflow id"); err != nil {
		return err
	}
	if err := required(run.WorkUnitID, "work unit id"); err != nil {
		return err
	}
	if run.Iteration < 0 {
		return errors.New("iteration must not be negative")
	}
	if run.Attempt < 0 {
		return errors.New("attempt must not be negative")
	}
	if err := required(run.ExtensionID, "extension id"); err != nil {
		return err
	}
	if err := required(run.HarnessBindingID, "harness binding id"); err != nil {
		return err
	}
	if err := run.Subject.validate(); err != nil {
		return err
	}
	if err := required(run.IdempotencyKey, "idempotency key"); err != nil {
		return err
	}
	if err := validateDigest(run.RequestDigest, "request digest"); err != nil {
		return err
	}
	if !run.Status.valid() {
		return fmt.Errorf("invalid component run status %q", run.Status)
	}
	if run.ExternalOperation != nil {
		if err := run.ExternalOperation.Validate(); err != nil {
			return err
		}
	}
	if run.Result != nil {
		if err := run.Result.Validate(); err != nil {
			return err
		}
	}
	if run.Error != nil {
		if err := run.Error.Validate(); err != nil {
			return err
		}
	}
	switch run.Status {
	case ComponentRunPending, ComponentRunRunning:
		if run.Result != nil || run.Error != nil {
			return errors.New("in-progress component run must not contain result or error")
		}
	case ComponentRunSucceeded:
		if run.Result == nil {
			return errors.New("succeeded component run requires result")
		}
		if run.Error != nil {
			return errors.New("succeeded component run must not contain error")
		}
	case ComponentRunFailed:
		if run.Error == nil {
			return errors.New("failed component run requires error")
		}
		if !run.Error.OutcomeKnown {
			return errors.New("failed component run requires known outcome")
		}
		if run.Result != nil {
			return errors.New("failed component run must not contain result")
		}
	case ComponentRunUnknown, ComponentRunCancelling:
		if run.ExternalOperation == nil {
			return errors.New("unknown or cancelling run requires an external operation for observe")
		}
		if run.Result != nil {
			return errors.New("unknown or cancelling run must not contain result")
		}
	case ComponentRunCancelled:
		if run.Result != nil || run.Error != nil {
			return errors.New("cancelled component run must not contain result or error")
		}
	}
	return nil
}

// RecoveryDecision is the fail-closed recovery disposition of a ComponentRun.
type RecoveryDecision struct {
	Terminal       bool
	NeedsObserve   bool
	RetryPermitted bool
	Escalate       bool
}

// RecoveryFor derives the platform-mandated recovery disposition. Unknown and
// Cancelling always require Observe; blind retry is never permitted and cancel
// acceptance is never terminal. Invalid runs cannot authorize a retry.
func RecoveryFor(run ComponentRun) RecoveryDecision {
	if err := run.Validate(); err != nil {
		if run.Status == ComponentRunUnknown || run.Status == ComponentRunCancelling {
			return RecoveryDecision{NeedsObserve: true, Escalate: true}
		}
		return RecoveryDecision{}
	}
	switch run.Status {
	case ComponentRunSucceeded, ComponentRunFailed, ComponentRunCancelled:
		return RecoveryDecision{Terminal: true}
	case ComponentRunUnknown:
		decision := RecoveryDecision{NeedsObserve: true}
		// Retry permission is intentionally never derived from public wire data.
		// Harness Host/Observe owns that decision after trusted external evidence.
		if run.Error != nil && run.Error.OutcomeKnown {
			decision.Escalate = true
		}
		return decision
	case ComponentRunCancelling:
		return RecoveryDecision{NeedsObserve: true}
	default:
		return RecoveryDecision{}
	}
}

// CallerAuthorityClaims is the set of production-authority claims a caller is
// never allowed to make: package roots, evaluator identity, isolation flags,
// RequestContext, permission ceilings, recovery claims and event lineage.
// Any non-empty claim is rejected fail-closed.
type CallerAuthorityClaims struct {
	PackageRoot       string             `json:"package_root,omitempty"`
	EvaluatorIdentity string             `json:"evaluator_identity,omitempty"`
	Isolation         string             `json:"isolation,omitempty"`
	PermissionCeiling []Permission       `json:"permission_ceiling,omitempty"`
	Recovery          string             `json:"recovery,omitempty"`
	Lineage           string             `json:"lineage,omitempty"`
	RequestContext    *RunRequestContext `json:"request_context,omitempty"`
}

// Validate rejects every caller-authored authority claim.
func (claims CallerAuthorityClaims) Validate() error {
	if claims.PackageRoot != "" {
		return errors.New("caller-authored package root is not authority")
	}
	if claims.EvaluatorIdentity != "" {
		return errors.New("caller-authored evaluator identity is not authority")
	}
	if claims.Isolation != "" {
		return errors.New("caller-authored isolation flags are not authority")
	}
	if len(claims.PermissionCeiling) > 0 {
		return errors.New("caller-authored permission ceiling is not authority")
	}
	if claims.Recovery != "" {
		return errors.New("caller-authored recovery claim is not authority")
	}
	if claims.Lineage != "" {
		return errors.New("caller-authored event lineage is not authority")
	}
	if claims.RequestContext != nil {
		return errors.New("caller-authored request context is not authority")
	}
	return nil
}

func validateSemver(value, name string) error {
	if err := required(value, name); err != nil {
		return err
	}
	if !semanticVersionPattern.MatchString(value) {
		return fmt.Errorf("%s %q is not a semantic version", name, value)
	}
	return nil
}

func validateDigest(value, name string) error {
	if err := required(value, name); err != nil {
		return err
	}
	if !sha256DigestPattern.MatchString(value) {
		return fmt.Errorf("%s must be a canonical sha256 digest", name)
	}
	return nil
}

func validateEntrypointPath(value string) error {
	if err := required(value, "entrypoint path"); err != nil {
		return err
	}
	if strings.HasPrefix(value, "/") || strings.HasPrefix(value, "\\") || isWindowsAbsolute(value) {
		return fmt.Errorf("entrypoint path %q must be package-relative", value)
	}
	for _, segment := range strings.FieldsFunc(value, func(r rune) bool { return r == '/' || r == '\\' }) {
		if segment == ".." {
			return fmt.Errorf("entrypoint path %q escapes the package boundary", value)
		}
	}
	return nil
}

func validatePackageRelativePath(value, name string) error {
	if err := required(value, name); err != nil {
		return err
	}
	if strings.HasPrefix(value, "/") || strings.HasPrefix(value, "\\") || isWindowsAbsolute(value) {
		return fmt.Errorf("%s must be package-relative", name)
	}
	for _, segment := range strings.FieldsFunc(value, func(r rune) bool { return r == '/' || r == '\\' }) {
		if segment == ".." {
			return fmt.Errorf("%s escapes the package boundary", name)
		}
	}
	return nil
}

func isWindowsAbsolute(value string) bool {
	return len(value) >= 2 && ((value[0] >= 'a' && value[0] <= 'z') || (value[0] >= 'A' && value[0] <= 'Z')) && value[1] == ':'
}

func containsPermission(permissions []Permission, target Permission) bool {
	for _, permission := range permissions {
		if permission == target {
			return true
		}
	}
	return false
}

func canonicalSHA256(value any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(encoded)
	return sha256Prefix + hex.EncodeToString(sum[:]), nil
}

func required(value, name string) error {
	if strings.TrimSpace(value) == "" {
		return fmt.Errorf("%s is required", name)
	}
	return nil
}

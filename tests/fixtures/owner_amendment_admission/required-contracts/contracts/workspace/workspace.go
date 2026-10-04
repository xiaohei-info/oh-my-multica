// Package workspace publishes the provider-independent WorkspacePort contract.
package workspace

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

var (
	// ErrInvalidRequest reports a WorkspaceRequest that is not explicit enough
	// for a provider to allocate safely.
	ErrInvalidRequest = errors.New("invalid workspace request")

	// ErrUnsupportedCapability reports a capability required by a request but
	// not declared by the selected provider.
	ErrUnsupportedCapability = errors.New("unsupported workspace capability")

	// ErrRequirementNotEnforced reports a provider that accepts a request while
	// failing to prove the requested persistence, resources, security, or retention.
	ErrRequirementNotEnforced = errors.New("workspace requirement not enforced")
)

// Capability is a fixed provider capability. Compute resources are requested
// through ResourceRequirements rather than represented as capabilities.
type Capability string

const (
	CapabilityPersistent               Capability = "persistent"
	CapabilitySuspendResume            Capability = "suspend_resume"
	CapabilitySnapshotRestore          Capability = "snapshot_restore"
	CapabilityWarmPool                 Capability = "warm_pool"
	CapabilityStableEndpoint           Capability = "stable_endpoint"
	CapabilityStrongIsolation          Capability = "strong_isolation"
	CapabilityNetworkPolicyEnforcement Capability = "network_policy_enforcement"
	CapabilityDedicatedPlacement       Capability = "dedicated_placement"
)

// Persistence describes whether a Workspace is retained across executions.
type Persistence string

const (
	PersistenceEphemeral  Persistence = "ephemeral"
	PersistencePersistent Persistence = "persistent"
)

// WorkspaceState is the provider-owned lifecycle state of a Workspace.
type WorkspaceState string

const (
	WorkspaceStateAllocating WorkspaceState = "allocating"
	WorkspaceStateReady      WorkspaceState = "ready"
	WorkspaceStateSuspended  WorkspaceState = "suspended"
	WorkspaceStateReleasing  WorkspaceState = "releasing"
	WorkspaceStateReleased   WorkspaceState = "released"
	WorkspaceStateLost       WorkspaceState = "lost"
)

const (
	defaultProviderConformanceTimeout      = 5 * time.Minute
	defaultProviderConformancePollInterval = 10 * time.Millisecond
	providerConformanceCleanupTimeout      = time.Second
)

// IsolationLevel describes the requested execution isolation strength.
type IsolationLevel string

const (
	IsolationStandard IsolationLevel = "standard"
	IsolationStrong   IsolationLevel = "strong"
)

// NetworkAccess describes the requested network policy mode.
type NetworkAccess string

const (
	// NetworkAllowed permits unrestricted network egress. It must not carry an
	// endpoint allowlist, which would otherwise be silently ignored.
	NetworkAllowed NetworkAccess = "allowed"
	// NetworkRestricted permits only the request's explicitly allowed endpoints.
	NetworkRestricted NetworkAccess = "restricted"
	// NetworkIsolated represents denied network access and permits no endpoints.
	NetworkIsolated NetworkAccess = "isolated"
)

// CredentialAccess describes how credentials may reach a Workspace.
type CredentialAccess string

const (
	CredentialsIsolated CredentialAccess = "isolated"
	CredentialsNone     CredentialAccess = "none"
)

// DataClassification describes the minimum data handling requirement.
type DataClassification string

const (
	DataInternal     DataClassification = "internal"
	DataConfidential DataClassification = "confidential"
)

// RetentionAction describes the provider action after release.
type RetentionAction string

const (
	RetentionDelete RetentionAction = "delete"
	RetentionRetain RetentionAction = "retain"
)

// WorkspaceSource is an immutable source materialized into a Workspace.
// Its closed set prevents providers from accepting ambiguous mutable sources.
type WorkspaceSource interface {
	workspaceSource()
}

// GitCommitSource identifies an exact Git commit.
type GitCommitSource struct {
	RepositoryURI string
	CommitSHA     string
}

func (GitCommitSource) workspaceSource() {}

// ArtifactSource identifies an immutable artifact and its content digest.
type ArtifactSource struct {
	ArtifactID    string
	ContentDigest string
}

func (ArtifactSource) workspaceSource() {}

// SnapshotSource identifies an immutable provider snapshot and its digest.
type SnapshotSource struct {
	SnapshotID     string
	SnapshotDigest string
}

func (SnapshotSource) workspaceSource() {}

// ResourceRequirements are explicit, provider-enforced resource requirements.
type ResourceRequirements struct {
	CPUMilli     int64
	MemoryBytes  int64
	GPUCount     int64
	StorageBytes int64
	Architecture string
	Topology     string
}

// SecurityRequirements are explicit provider-enforced security requirements.
type SecurityRequirements struct {
	Isolation IsolationLevel
	Network   NetworkAccess
	// AllowedEndpoints is the absolute-URI allowlist for restricted networking.
	// It must be non-empty for NetworkRestricted and empty for NetworkAllowed
	// and NetworkIsolated.
	AllowedEndpoints []string
	Credentials      CredentialAccess
	Data             DataClassification
}

// RetentionPolicy makes release and expiry behavior explicit.
type RetentionPolicy struct {
	OnRelease RetentionAction
	ExpiresAt time.Time
}

// WorkspaceRequirementObservation is the provider's observable record of the
// non-secret requirements enforced for a Workspace.
type WorkspaceRequirementObservation struct {
	Persistence Persistence
	Resources   ResourceRequirements
	Security    SecurityRequirements
	Retention   RetentionPolicy
}

// WorkspaceRequest is the provider-independent request created by a Workspace
// Adapter. It intentionally contains no product role or provider selection.
type WorkspaceRequest struct {
	RequestID            string
	WorkspacePurpose     string
	Source               WorkspaceSource
	Persistence          Persistence
	ResourceRequirements ResourceRequirements
	SecurityRequirements SecurityRequirements
	RetentionPolicy      RetentionPolicy
	IdempotencyKey       string
	RequiredCapabilities []Capability
}

// WorkspaceEndpoint is an authorized access point owned by a provider.
type WorkspaceEndpoint struct {
	Kind   string
	URI    string
	Access []string
}

// WorkspaceHandle is the persisted provider reference returned to upper layers.
type WorkspaceHandle struct {
	WorkspaceID          string
	ProviderKey          string
	State                WorkspaceState
	Endpoints            []WorkspaceEndpoint
	PersistenceHandle    string
	ObservedCapabilities []Capability
	CreatedAt            time.Time
	ExpiresAt            time.Time
}

// WorkspaceCondition records a provider-observed exceptional lifecycle fact.
type WorkspaceCondition struct {
	Type       string
	Reason     string
	Message    string
	ObservedAt time.Time
}

// WorkspaceFailure is a structured provider failure without secret material.
type WorkspaceFailure struct {
	Code      string
	Message   string
	Retryable bool
}

// WorkspaceObservation is the provider-owned view returned by inspect/release.
type WorkspaceObservation struct {
	Handle       WorkspaceHandle
	Requirements WorkspaceRequirementObservation
	Conditions   []WorkspaceCondition
	Failure      *WorkspaceFailure
	ObservedAt   time.Time
}

// WorkspacePort owns the lifecycle operations exposed to a Workspace Adapter.
type WorkspacePort interface {
	Acquire(context.Context, WorkspaceRequest) (WorkspaceHandle, error)
	Inspect(context.Context, WorkspaceHandle) (WorkspaceObservation, error)
	Recover(context.Context, WorkspaceHandle) (WorkspaceHandle, error)
	Release(context.Context, WorkspaceHandle) (WorkspaceObservation, error)
}

// WorkspaceProvider is an internal WorkspacePort implementation selected by
// platform policy and cluster capability, never by product input.
type WorkspaceProvider interface {
	WorkspacePort
	// ProviderKey returns an immutable provider identity without blocking past ctx.
	ProviderKey(context.Context) (string, error)
	// Capabilities returns an immutable capability snapshot without blocking past ctx.
	Capabilities(context.Context) ([]Capability, error)
}

// Validate rejects requests that leave resource, security, retention, source,
// idempotency, or capability requirements ambiguous.
func (request WorkspaceRequest) Validate() error {
	if request.RequestID == "" || request.WorkspacePurpose == "" || request.IdempotencyKey == "" {
		return ErrInvalidRequest
	}
	if request.Persistence != PersistenceEphemeral && request.Persistence != PersistencePersistent {
		return ErrInvalidRequest
	}
	if err := validateSource(request.Source); err != nil {
		return err
	}
	if !request.ResourceRequirements.valid() || !request.SecurityRequirements.valid() || !request.RetentionPolicy.valid() {
		return ErrInvalidRequest
	}
	for _, capability := range request.RequiredCapabilities {
		if !isCapability(capability) {
			return ErrInvalidRequest
		}
	}
	return nil
}

func validateSource(source WorkspaceSource) error {
	_, err := normalizeSource(source)
	return err
}

func normalizeSource(source WorkspaceSource) (WorkspaceSource, error) {
	if source == nil {
		return nil, nil
	}
	switch source := source.(type) {
	case GitCommitSource:
		if isValidGitCommitSource(source) {
			return source, nil
		}
	case *GitCommitSource:
		if source != nil && isValidGitCommitSource(*source) {
			return *source, nil
		}
	case ArtifactSource:
		if isValidArtifactSource(source) {
			return source, nil
		}
	case *ArtifactSource:
		if source != nil && isValidArtifactSource(*source) {
			return *source, nil
		}
	case SnapshotSource:
		if isValidSnapshotSource(source) {
			return source, nil
		}
	case *SnapshotSource:
		if source != nil && isValidSnapshotSource(*source) {
			return *source, nil
		}
	}
	return nil, ErrInvalidRequest
}

func isValidGitCommitSource(source GitCommitSource) bool {
	return isValidAbsoluteURI(source.RepositoryURI) && isGitObjectID(source.CommitSHA)
}

func isValidArtifactSource(source ArtifactSource) bool {
	return source.ArtifactID != "" && isContentDigest(source.ContentDigest)
}

func isValidSnapshotSource(source SnapshotSource) bool {
	return source.SnapshotID != "" && isContentDigest(source.SnapshotDigest)
}

func isGitObjectID(value string) bool {
	return (len(value) == 40 || len(value) == 64) && isHex(value) && !isAllZero(value)
}

func isAllZero(value string) bool {
	for _, character := range value {
		if character != '0' {
			return false
		}
	}
	return true
}

func isContentDigest(value string) bool {
	algorithm, encoded, ok := strings.Cut(value, ":")
	if !ok {
		return false
	}
	switch algorithm {
	case "sha256":
		return len(encoded) == 64 && isHex(encoded)
	case "sha512":
		return len(encoded) == 128 && isHex(encoded)
	default:
		return false
	}
}

func isHex(value string) bool {
	for _, character := range value {
		if !(character >= '0' && character <= '9') && !(character >= 'a' && character <= 'f') && !(character >= 'A' && character <= 'F') {
			return false
		}
	}
	return true
}

func (requirements ResourceRequirements) valid() bool {
	return requirements.CPUMilli > 0 &&
		requirements.MemoryBytes > 0 &&
		requirements.GPUCount >= 0 &&
		requirements.StorageBytes > 0 &&
		requirements.Architecture != "" &&
		requirements.Topology != ""
}

func (requirements SecurityRequirements) valid() bool {
	if (requirements.Isolation != IsolationStandard && requirements.Isolation != IsolationStrong) ||
		(requirements.Credentials != CredentialsIsolated && requirements.Credentials != CredentialsNone) ||
		(requirements.Data != DataInternal && requirements.Data != DataConfidential) {
		return false
	}
	switch requirements.Network {
	case NetworkAllowed:
		return len(requirements.AllowedEndpoints) == 0
	case NetworkRestricted:
		return validAllowedEndpoints(requirements.AllowedEndpoints, true)
	case NetworkIsolated:
		return len(requirements.AllowedEndpoints) == 0
	default:
		return false
	}
}

func validAllowedEndpoints(endpoints []string, required bool) bool {
	if required && len(endpoints) == 0 {
		return false
	}
	seen := make(map[string]struct{}, len(endpoints))
	for _, endpoint := range endpoints {
		if !isValidAbsoluteURI(endpoint) {
			return false
		}
		if _, duplicate := seen[endpoint]; duplicate {
			return false
		}
		seen[endpoint] = struct{}{}
	}
	return true
}

func isValidAbsoluteURI(value string) bool {
	if value == "" || strings.TrimSpace(value) != value {
		return false
	}
	parsed, err := url.ParseRequestURI(value)
	return err == nil && parsed.Scheme != "" && parsed.Host != ""
}

func (policy RetentionPolicy) valid() bool {
	return (policy.OnRelease == RetentionDelete || policy.OnRelease == RetentionRetain) && !policy.ExpiresAt.IsZero()
}

func isCapability(capability Capability) bool {
	switch capability {
	case CapabilityPersistent,
		CapabilitySuspendResume,
		CapabilitySnapshotRestore,
		CapabilityWarmPool,
		CapabilityStableEndpoint,
		CapabilityStrongIsolation,
		CapabilityNetworkPolicyEnforcement,
		CapabilityDedicatedPlacement:
		return true
	default:
		return false
	}
}

// VerifyProviderConformance executes the provider contract using a deterministic
// request. It is intended for provider conformance tests, not production flows.
func VerifyProviderConformance(ctx context.Context, provider WorkspaceProvider, request WorkspaceRequest) error {
	return VerifyProviderConformanceWithOptions(ctx, provider, request, ProviderConformanceOptions{})
}

// ProviderConformanceOptions configures verification only when the caller has
// not already supplied a context deadline.
type ProviderConformanceOptions struct {
	Timeout      time.Duration
	PollInterval time.Duration
}

// VerifyProviderConformanceWithOptions verifies a provider using the caller's
// deadline for the entire lifecycle when one is present.
func VerifyProviderConformanceWithOptions(ctx context.Context, provider WorkspaceProvider, request WorkspaceRequest, options ProviderConformanceOptions) error {
	normalizedOptions, err := options.normalized()
	if err != nil {
		return err
	}
	return verifyProviderConformance(ctx, provider, request, normalizedOptions, func(ctx context.Context) error {
		return waitForConformancePoll(ctx, normalizedOptions.PollInterval)
	})
}

func verifyProviderConformance(ctx context.Context, provider WorkspaceProvider, request WorkspaceRequest, options ProviderConformanceOptions, poll func(context.Context) error) (resultErr error) {
	if ctx == nil {
		return fmt.Errorf("workspace provider: %w", ErrInvalidRequest)
	}
	conformanceContext, cancel := conformanceContext(ctx, options.Timeout)
	defer cancel()
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if provider == nil {
		return fmt.Errorf("workspace provider: %w", ErrInvalidRequest)
	}
	cleanup := workspaceCleanup{}
	normalizedRequest, err := request.normalized()
	if err != nil {
		return err
	}
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	providerKey, err := provider.ProviderKey(conformanceContext)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if err != nil {
		return fmt.Errorf("provider key: %w", err)
	}
	if providerKey == "" {
		return ErrInvalidRequest
	}
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	providerCapabilities, err := provider.Capabilities(conformanceContext)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if err != nil {
		return fmt.Errorf("provider capabilities: %w", err)
	}
	providerCapabilities = cloneCapabilities(providerCapabilities)
	if !validCapabilities(providerCapabilities) || !supportsAll(providerCapabilities, normalizedRequest.requiredCapabilities("")) {
		return ErrUnsupportedCapability
	}
	stableEndpoints := stableEndpointBaseline{}
	defer func() {
		if resultErr != nil {
			cleanup.releaseAll(provider, providerKey, providerCapabilities, normalizedRequest)
		}
	}()
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	first, err := provider.Acquire(conformanceContext, normalizedRequest.clone())
	first = cloneWorkspaceHandle(first)
	cleanup.trackCandidate(first)
	if err == nil {
		cleanup.confirm(first, providerKey, normalizedRequest)
	}
	if contextErr := conformanceContext.Err(); contextErr != nil {
		return contextErr
	}
	if err != nil {
		return fmt.Errorf("acquire workspace: %w", err)
	}
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	firstValidationErr := validateInitialWorkspaceHandle(first, providerKey, providerCapabilities, normalizedRequest)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if firstValidationErr != nil {
		return fmt.Errorf("acquire workspace: %w", firstValidationErr)
	}
	if err := stableEndpoints.observe(first, normalizedRequest); err != nil {
		return fmt.Errorf("acquire workspace: %w", err)
	}
	second, err := provider.Acquire(conformanceContext, normalizedRequest.clone())
	second = cloneWorkspaceHandle(second)
	cleanup.trackCandidate(second)
	if err == nil {
		cleanup.confirm(second, providerKey, normalizedRequest)
	}
	if contextErr := conformanceContext.Err(); contextErr != nil {
		return contextErr
	}
	if err != nil {
		return fmt.Errorf("replay acquire workspace: %w", err)
	}
	secondValidationErr := validateWorkspaceHandle(second, providerKey, providerCapabilities, normalizedRequest)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if secondValidationErr != nil {
		return fmt.Errorf("replay acquire workspace: %w", secondValidationErr)
	}
	sameIdentity := sameWorkspaceIdentity(first, second, normalizedRequest)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if !sameIdentity {
		return fmt.Errorf("replay acquire workspace: %w", ErrInvalidRequest)
	}
	validTransition := isValidPreReleaseTransition(first.State, second.State)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if !validTransition {
		return fmt.Errorf("replay acquire workspace: %w", ErrInvalidRequest)
	}
	if err := stableEndpoints.observe(second, normalizedRequest); err != nil {
		return fmt.Errorf("replay acquire workspace: %w", err)
	}
	current, err := inspectUntilAllocationResolved(conformanceContext, provider, second, providerKey, providerCapabilities, normalizedRequest, &stableEndpoints, &cleanup, poll)
	if err != nil {
		return fmt.Errorf("inspect workspace: %w", err)
	}
	current, err = recoverWorkspace(conformanceContext, provider, current, providerKey, providerCapabilities, normalizedRequest, &stableEndpoints, &cleanup)
	if err != nil {
		return fmt.Errorf("recover workspace: %w", err)
	}
	current, err = inspectUntilAllocationResolved(conformanceContext, provider, current, providerKey, providerCapabilities, normalizedRequest, &stableEndpoints, &cleanup, poll)
	if err != nil {
		return fmt.Errorf("inspect recovered workspace: %w", err)
	}
	if current.State == WorkspaceStateLost {
		current, err = recoverWorkspace(conformanceContext, provider, current, providerKey, providerCapabilities, normalizedRequest, &stableEndpoints, &cleanup)
		if err != nil {
			return fmt.Errorf("recover lost workspace: %w", err)
		}
		current, err = inspectUntilAllocationResolved(conformanceContext, provider, current, providerKey, providerCapabilities, normalizedRequest, &stableEndpoints, &cleanup, poll)
		if err != nil {
			return fmt.Errorf("inspect recovered lost workspace: %w", err)
		}
	}
	if current.State != WorkspaceStateReady {
		if err := conformanceContext.Err(); err != nil {
			return err
		}
		return fmt.Errorf("workspace did not become ready: %w", ErrInvalidRequest)
	}
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	cleanup.releaseAttempted(current)
	released, err := provider.Release(conformanceContext, cloneWorkspaceHandle(current))
	if contextErr := conformanceContext.Err(); contextErr != nil {
		return contextErr
	}
	if err != nil {
		return fmt.Errorf("release workspace: %w", err)
	}
	released.Handle = cloneWorkspaceHandle(released.Handle)
	releaseValidationErr := validateReleaseObservation(current, released, providerKey, providerCapabilities, normalizedRequest)
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	if releaseValidationErr != nil {
		return fmt.Errorf("release workspace: %w", releaseValidationErr)
	}
	if released.Handle.State == WorkspaceStateReleased {
		if err := conformanceContext.Err(); err != nil {
			return err
		}
		cleanup.markReleased(current)
		return verifyProviderRejectsUnsupportedRequests(conformanceContext, provider, normalizedRequest, providerCapabilities)
	}
	if err := inspectUntilReleased(conformanceContext, provider, released.Handle, providerKey, providerCapabilities, normalizedRequest, poll); err != nil {
		return fmt.Errorf("inspect released workspace: %w", err)
	}
	if err := conformanceContext.Err(); err != nil {
		return err
	}
	cleanup.markReleased(current)
	return verifyProviderRejectsUnsupportedRequests(conformanceContext, provider, normalizedRequest, providerCapabilities)
}

func (options ProviderConformanceOptions) normalized() (ProviderConformanceOptions, error) {
	if options.Timeout < 0 || options.PollInterval < 0 {
		return ProviderConformanceOptions{}, ErrInvalidRequest
	}
	if options.Timeout == 0 {
		options.Timeout = defaultProviderConformanceTimeout
	}
	if options.PollInterval == 0 {
		options.PollInterval = defaultProviderConformancePollInterval
	}
	return options, nil
}

func conformanceContext(ctx context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if _, hasDeadline := ctx.Deadline(); hasDeadline {
		return ctx, func() {}
	}
	return context.WithTimeout(ctx, timeout)
}

func waitForConformancePoll(ctx context.Context, pollInterval time.Duration) error {
	timer := time.NewTimer(pollInterval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func verifyProviderRejectsUnsupportedRequests(ctx context.Context, provider WorkspaceProvider, request WorkspaceRequest, providerCapabilities []Capability) error {
	for _, probe := range unsupportedCapabilityProbes(request, providerCapabilities) {
		handle, err := provider.Acquire(ctx, probe)
		if contextErr := ctx.Err(); contextErr != nil {
			return contextErr
		}
		handle = cloneWorkspaceHandle(handle)
		if !errors.Is(err, ErrUnsupportedCapability) || hasWorkspaceHandleValue(handle) {
			// Unsupported probes have not established an acquired identity. In
			// particular, an arbitrary returned handle must not be released.
			return ErrUnsupportedCapability
		}
	}
	return nil
}

func unsupportedCapabilityProbes(request WorkspaceRequest, providerCapabilities []Capability) []WorkspaceRequest {
	probes := make([]WorkspaceRequest, 0, 9)
	for _, capability := range []Capability{
		CapabilityWarmPool,
		CapabilityDedicatedPlacement,
		CapabilityStableEndpoint,
		CapabilitySnapshotRestore,
		CapabilitySuspendResume,
		CapabilityPersistent,
		CapabilityStrongIsolation,
		CapabilityNetworkPolicyEnforcement,
	} {
		if !hasCapability(providerCapabilities, capability) {
			explicit := request.clone()
			explicit.RequestID += "-unsupported-" + string(capability)
			explicit.IdempotencyKey += "-unsupported-" + string(capability)
			explicit.RequiredCapabilities = append(explicit.RequiredCapabilities, capability)
			probes = append(probes, explicit)
		}
	}
	if !hasCapability(providerCapabilities, CapabilitySnapshotRestore) {
		derived := request.clone()
		derived.RequestID += "-unsupported-derived-snapshot-restore"
		derived.IdempotencyKey += "-unsupported-derived-snapshot-restore"
		derived.Source = SnapshotSource{
			SnapshotID:     "conformance-unsupported-snapshot",
			SnapshotDigest: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		}
		probes = append(probes, derived)
	}
	return probes
}

func hasWorkspaceHandleValue(handle WorkspaceHandle) bool {
	return handle.WorkspaceID != "" ||
		handle.ProviderKey != "" ||
		handle.State != "" ||
		len(handle.Endpoints) != 0 ||
		handle.PersistenceHandle != "" ||
		len(handle.ObservedCapabilities) != 0 ||
		!handle.CreatedAt.IsZero() ||
		!handle.ExpiresAt.IsZero()
}

type stableEndpointBaseline struct {
	endpoints []WorkspaceEndpoint
}

func (baseline *stableEndpointBaseline) observe(handle WorkspaceHandle, request WorkspaceRequest) error {
	if !requiresStableEndpoints(request, handle) || !isPreReleaseState(handle.State) || len(handle.Endpoints) == 0 {
		return nil
	}
	if len(baseline.endpoints) != 0 && !sameWorkspaceEndpoints(baseline.endpoints, handle.Endpoints) {
		return ErrInvalidRequest
	}
	if len(baseline.endpoints) == 0 {
		baseline.endpoints = cloneWorkspaceEndpoints(handle.Endpoints)
	}
	return nil
}

type workspaceCleanupEntry struct {
	handle           WorkspaceHandle
	confirmed        bool
	releaseAttempted bool
}

type workspaceCleanup struct {
	entries []workspaceCleanupEntry
}

func (cleanup *workspaceCleanup) trackCandidate(handle WorkspaceHandle) {
	if !hasWorkspaceHandleValue(handle) {
		return
	}
	for _, entry := range cleanup.entries {
		if sameCleanupWorkspaceHandle(entry.handle, handle) {
			return
		}
	}
	cleanup.entries = append(cleanup.entries, workspaceCleanupEntry{handle: cloneWorkspaceHandle(handle)})
}

func (cleanup *workspaceCleanup) confirm(handle WorkspaceHandle, providerKey string, request WorkspaceRequest) bool {
	if !safeWorkspaceCleanupIdentity(handle, providerKey, request) {
		return false
	}
	for index := range cleanup.entries {
		if sameCleanupWorkspaceHandle(cleanup.entries[index].handle, handle) {
			cleanup.entries[index].handle = cloneWorkspaceHandle(handle)
			cleanup.entries[index].confirmed = true
			return true
		}
	}
	cleanup.entries = append(cleanup.entries, workspaceCleanupEntry{handle: cloneWorkspaceHandle(handle), confirmed: true})
	return true
}

func (cleanup *workspaceCleanup) releaseAttempted(handle WorkspaceHandle) {
	for index := range cleanup.entries {
		if sameCleanupWorkspaceHandle(cleanup.entries[index].handle, handle) {
			cleanup.entries[index].releaseAttempted = true
		}
	}
}

func (cleanup *workspaceCleanup) replace(previous, current WorkspaceHandle, providerKey string, request WorkspaceRequest) {
	if !cleanup.confirm(current, providerKey, request) {
		return
	}
	if sameCleanupWorkspaceHandle(previous, current) {
		return
	}
	cleanup.markReleased(previous)
}

func (cleanup *workspaceCleanup) markReleased(handle WorkspaceHandle) {
	for index := range cleanup.entries {
		if sameCleanupWorkspaceHandle(cleanup.entries[index].handle, handle) {
			cleanup.entries[index].handle = WorkspaceHandle{}
		}
	}
}

func (cleanup workspaceCleanup) releaseAll(provider WorkspaceProvider, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) {
	for _, entry := range cleanup.entries {
		if !entry.confirmed || !hasWorkspaceHandleValue(entry.handle) {
			continue
		}
		if !entry.releaseAttempted {
			cleanupContext, cancel := context.WithTimeout(context.Background(), providerConformanceCleanupTimeout)
			_, _ = provider.Release(cleanupContext, cloneWorkspaceHandle(entry.handle))
			cancel()
			continue
		}
		inspectContext, cancelInspect := context.WithTimeout(context.Background(), providerConformanceCleanupTimeout)
		observation, err := provider.Inspect(inspectContext, cloneWorkspaceHandle(entry.handle))
		cancelInspect()
		if err == nil && sameCleanupWorkspaceHandle(entry.handle, observation.Handle) && validateWorkspaceHandle(observation.Handle, providerKey, providerCapabilities, request) == nil {
			releaseContext, cancelRelease := context.WithTimeout(context.Background(), providerConformanceCleanupTimeout)
			_, _ = provider.Release(releaseContext, cloneWorkspaceHandle(observation.Handle))
			cancelRelease()
		}
	}
}

func safeWorkspaceCleanupIdentity(handle WorkspaceHandle, providerKey string, request WorkspaceRequest) bool {
	return handle.WorkspaceID != "" &&
		handle.ProviderKey == providerKey &&
		!handle.CreatedAt.IsZero() &&
		handle.CreatedAt.Before(handle.ExpiresAt) &&
		handle.ExpiresAt.Equal(request.RetentionPolicy.ExpiresAt) &&
		((request.Persistence == PersistencePersistent && handle.PersistenceHandle != "") ||
			(request.Persistence == PersistenceEphemeral && handle.PersistenceHandle == ""))
}

func sameCleanupWorkspaceHandle(left, right WorkspaceHandle) bool {
	return left.WorkspaceID == right.WorkspaceID &&
		left.ProviderKey == right.ProviderKey &&
		left.PersistenceHandle == right.PersistenceHandle &&
		left.CreatedAt.Equal(right.CreatedAt) &&
		left.ExpiresAt.Equal(right.ExpiresAt)
}

func inspectUntilAllocationResolved(ctx context.Context, provider WorkspaceProvider, current WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest, stableEndpoints *stableEndpointBaseline, cleanup *workspaceCleanup, poll func(context.Context) error) (WorkspaceHandle, error) {
	for {
		if err := ctx.Err(); err != nil {
			return WorkspaceHandle{}, err
		}
		observation, err := provider.Inspect(ctx, cloneWorkspaceHandle(current))
		if contextErr := ctx.Err(); contextErr != nil {
			return WorkspaceHandle{}, contextErr
		}
		if err != nil {
			return WorkspaceHandle{}, err
		}
		observation.Handle = cloneWorkspaceHandle(observation.Handle)
		cleanup.trackCandidate(observation.Handle)
		validationErr := validateWorkspaceObservation(current, observation, providerKey, providerCapabilities, request)
		if err := ctx.Err(); err != nil {
			return WorkspaceHandle{}, err
		}
		if validationErr != nil {
			return WorkspaceHandle{}, validationErr
		}
		if err := stableEndpoints.observe(observation.Handle, request); err != nil {
			return WorkspaceHandle{}, err
		}
		cleanup.replace(current, observation.Handle, providerKey, request)
		current = cloneWorkspaceHandle(observation.Handle)
		if err := ctx.Err(); err != nil {
			return WorkspaceHandle{}, err
		}
		if current.State != WorkspaceStateAllocating {
			if err := ctx.Err(); err != nil {
				return WorkspaceHandle{}, err
			}
			return current, nil
		}
		if err := poll(ctx); err != nil {
			return WorkspaceHandle{}, err
		}
	}
}

func inspectUntilReleased(ctx context.Context, provider WorkspaceProvider, current WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest, poll func(context.Context) error) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		observation, err := provider.Inspect(ctx, cloneWorkspaceHandle(current))
		if contextErr := ctx.Err(); contextErr != nil {
			return contextErr
		}
		if err != nil {
			return err
		}
		observation.Handle = cloneWorkspaceHandle(observation.Handle)
		validationErr := validateReleaseObservation(current, observation, providerKey, providerCapabilities, request)
		if err := ctx.Err(); err != nil {
			return err
		}
		if validationErr != nil {
			return validationErr
		}
		current = cloneWorkspaceHandle(observation.Handle)
		if err := ctx.Err(); err != nil {
			return err
		}
		if current.State == WorkspaceStateReleased {
			if err := ctx.Err(); err != nil {
				return err
			}
			return nil
		}
		if err := poll(ctx); err != nil {
			return err
		}
	}
}

func recoverWorkspace(ctx context.Context, provider WorkspaceProvider, handle WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest, stableEndpoints *stableEndpointBaseline, cleanup *workspaceCleanup) (WorkspaceHandle, error) {
	if err := ctx.Err(); err != nil {
		return WorkspaceHandle{}, err
	}
	recovered, err := provider.Recover(ctx, cloneWorkspaceHandle(handle))
	recovered = cloneWorkspaceHandle(recovered)
	cleanup.trackCandidate(recovered)
	if err == nil && sameWorkspaceRecoveryLineage(handle, recovered) {
		cleanup.replace(handle, recovered, providerKey, request)
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return WorkspaceHandle{}, contextErr
	}
	if err != nil {
		return WorkspaceHandle{}, err
	}
	validationErr := validateWorkspaceHandle(recovered, providerKey, providerCapabilities, request)
	if err := ctx.Err(); err != nil {
		return WorkspaceHandle{}, err
	}
	if validationErr != nil {
		return WorkspaceHandle{}, validationErr
	}
	sameIdentity := sameWorkspaceRecoveryIdentity(handle, recovered, request)
	validTransition := isValidRecoveryTransition(handle.State, recovered.State)
	if err := ctx.Err(); err != nil {
		return WorkspaceHandle{}, err
	}
	if !sameIdentity || !validTransition {
		return WorkspaceHandle{}, ErrInvalidRequest
	}
	if err := stableEndpoints.observe(recovered, request); err != nil {
		return WorkspaceHandle{}, err
	}
	if err := ctx.Err(); err != nil {
		return WorkspaceHandle{}, err
	}
	return recovered, nil
}

func validateWorkspaceHandle(handle WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) error {
	if err := validateWorkspaceHandleIdentity(handle, providerKey, providerCapabilities, request); err != nil {
		return err
	}
	if !isPreReleaseState(handle.State) {
		return ErrInvalidRequest
	}
	return nil
}

func validateInitialWorkspaceHandle(handle WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) error {
	if err := validateWorkspaceHandleIdentity(handle, providerKey, providerCapabilities, request); err != nil {
		return err
	}
	if handle.State != WorkspaceStateAllocating && handle.State != WorkspaceStateReady {
		return ErrInvalidRequest
	}
	return nil
}

func validateWorkspaceHandleIdentity(handle WorkspaceHandle, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) error {
	if providerKey == "" || handle.WorkspaceID == "" || handle.ProviderKey == "" || handle.ProviderKey != providerKey || handle.CreatedAt.IsZero() || handle.ExpiresAt.IsZero() || !handle.CreatedAt.Before(handle.ExpiresAt) || !handle.ExpiresAt.Equal(request.RetentionPolicy.ExpiresAt) {
		return ErrInvalidRequest
	}
	if request.Persistence == PersistencePersistent && handle.PersistenceHandle == "" {
		return ErrRequirementNotEnforced
	}
	if request.Persistence == PersistenceEphemeral && handle.PersistenceHandle != "" {
		return ErrRequirementNotEnforced
	}
	if !validCapabilities(handle.ObservedCapabilities) || !supportsAll(providerCapabilities, handle.ObservedCapabilities) {
		return ErrUnsupportedCapability
	}
	if !validWorkspaceEndpoints(handle.Endpoints) {
		return ErrInvalidRequest
	}
	if requiresStableEndpoints(request, handle) && isUsableWorkspaceState(handle.State) && len(handle.Endpoints) == 0 {
		return ErrRequirementNotEnforced
	}
	requiredCapabilities := request.requiredCapabilities(handle.State)
	if !supportsAll(providerCapabilities, requiredCapabilities) || !supportsAll(handle.ObservedCapabilities, requiredCapabilities) {
		return ErrUnsupportedCapability
	}
	return nil
}

func validateWorkspaceObservation(handle WorkspaceHandle, observation WorkspaceObservation, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) error {
	if err := validateWorkspaceHandleIdentity(observation.Handle, providerKey, providerCapabilities, request); err != nil {
		return err
	}
	if !sameWorkspaceIdentity(handle, observation.Handle, request) {
		return ErrInvalidRequest
	}
	if observation.Handle.State == WorkspaceStateLost {
		if !isPreReleaseState(handle.State) {
			return ErrInvalidRequest
		}
	} else if !isPreReleaseState(observation.Handle.State) || !isValidPreReleaseTransition(handle.State, observation.Handle.State) {
		return ErrInvalidRequest
	}
	if !sameRequirementObservation(request, observation.Requirements) {
		return ErrRequirementNotEnforced
	}
	return nil
}

func (request WorkspaceRequest) requiredCapabilities(state WorkspaceState) []Capability {
	required := append([]Capability(nil), request.RequiredCapabilities...)
	if request.Persistence == PersistencePersistent {
		required = append(required, CapabilityPersistent)
	}
	if request.SecurityRequirements.Isolation == IsolationStrong {
		required = append(required, CapabilityStrongIsolation)
	}
	if request.SecurityRequirements.Network == NetworkAllowed || request.SecurityRequirements.Network == NetworkRestricted || request.SecurityRequirements.Network == NetworkIsolated {
		required = append(required, CapabilityNetworkPolicyEnforcement)
	}
	switch source := request.Source.(type) {
	case SnapshotSource:
		required = append(required, CapabilitySnapshotRestore)
	case *SnapshotSource:
		if source != nil {
			required = append(required, CapabilitySnapshotRestore)
		}
	}
	if state == WorkspaceStateSuspended {
		required = append(required, CapabilitySuspendResume)
	}
	return required
}

func validateReleaseObservation(handle WorkspaceHandle, observation WorkspaceObservation, providerKey string, providerCapabilities []Capability, request WorkspaceRequest) error {
	if err := validateWorkspaceHandleIdentity(observation.Handle, providerKey, providerCapabilities, request); err != nil {
		return err
	}
	if !sameWorkspaceIdentity(handle, observation.Handle, request) || (observation.Handle.State != WorkspaceStateReleasing && observation.Handle.State != WorkspaceStateReleased) {
		return ErrInvalidRequest
	}
	if !sameRequirementObservation(request, observation.Requirements) {
		return ErrRequirementNotEnforced
	}
	return nil
}

func sameWorkspaceIdentity(expected, actual WorkspaceHandle, request WorkspaceRequest) bool {
	if expected.WorkspaceID != actual.WorkspaceID ||
		expected.ProviderKey != actual.ProviderKey ||
		expected.PersistenceHandle != actual.PersistenceHandle ||
		!expected.CreatedAt.Equal(actual.CreatedAt) ||
		!expected.ExpiresAt.Equal(actual.ExpiresAt) ||
		!sameCapabilities(expected.ObservedCapabilities, actual.ObservedCapabilities) {
		return false
	}
	return stableWorkspaceEndpointsMatch(expected, actual, request)
}

func sameWorkspaceRecoveryIdentity(expected, actual WorkspaceHandle, request WorkspaceRequest) bool {
	if expected.State != WorkspaceStateLost {
		return sameWorkspaceIdentity(expected, actual, request)
	}
	if !sameWorkspaceRecoveryLineage(expected, actual) || !sameCapabilities(expected.ObservedCapabilities, actual.ObservedCapabilities) {
		return false
	}
	return true
}

func sameWorkspaceRecoveryLineage(expected, actual WorkspaceHandle) bool {
	return expected.State == WorkspaceStateLost &&
		expected.WorkspaceID == actual.WorkspaceID &&
		expected.ProviderKey == actual.ProviderKey &&
		!expected.CreatedAt.IsZero() &&
		expected.CreatedAt.Equal(actual.CreatedAt) &&
		!expected.ExpiresAt.IsZero() &&
		expected.ExpiresAt.Equal(actual.ExpiresAt)
}

func stableWorkspaceEndpointsMatch(expected, actual WorkspaceHandle, request WorkspaceRequest) bool {
	if !requiresStableEndpoints(request, expected, actual) {
		return true
	}
	if isTeardownWorkspaceState(actual.State) {
		return len(actual.Endpoints) == 0 || sameWorkspaceEndpoints(expected.Endpoints, actual.Endpoints)
	}
	if expected.State == WorkspaceStateAllocating && isPreReleaseState(actual.State) && len(expected.Endpoints) == 0 {
		return true
	}
	return sameWorkspaceEndpoints(expected.Endpoints, actual.Endpoints)
}

func sameRequirementObservation(request WorkspaceRequest, observation WorkspaceRequirementObservation) bool {
	return request.Persistence == observation.Persistence &&
		request.ResourceRequirements == observation.Resources &&
		sameSecurityRequirements(request.SecurityRequirements, observation.Security) &&
		request.RetentionPolicy.OnRelease == observation.Retention.OnRelease &&
		request.RetentionPolicy.ExpiresAt.Equal(observation.Retention.ExpiresAt)
}

func isPreReleaseState(state WorkspaceState) bool {
	switch state {
	case WorkspaceStateAllocating, WorkspaceStateReady, WorkspaceStateSuspended:
		return true
	default:
		return false
	}
}

func isUsableWorkspaceState(state WorkspaceState) bool {
	return state == WorkspaceStateReady || state == WorkspaceStateSuspended
}

func isTeardownWorkspaceState(state WorkspaceState) bool {
	return state == WorkspaceStateLost || state == WorkspaceStateReleasing || state == WorkspaceStateReleased
}

func isValidPreReleaseTransition(from, to WorkspaceState) bool {
	switch from {
	case WorkspaceStateAllocating:
		return to == WorkspaceStateAllocating || to == WorkspaceStateReady
	case WorkspaceStateReady:
		return to == WorkspaceStateReady || to == WorkspaceStateSuspended
	case WorkspaceStateSuspended:
		return to == WorkspaceStateReady || to == WorkspaceStateSuspended
	default:
		return false
	}
}

func isValidRecoveryTransition(from, to WorkspaceState) bool {
	if from == WorkspaceStateLost {
		return to == WorkspaceStateAllocating || to == WorkspaceStateReady
	}
	return isValidPreReleaseTransition(from, to)
}

func (request WorkspaceRequest) requirementObservation() WorkspaceRequirementObservation {
	return WorkspaceRequirementObservation{
		Persistence: request.Persistence,
		Resources:   request.ResourceRequirements,
		Security:    request.SecurityRequirements,
		Retention:   request.RetentionPolicy,
	}
}

func (request WorkspaceRequest) normalized() (WorkspaceRequest, error) {
	if err := request.Validate(); err != nil {
		return WorkspaceRequest{}, err
	}
	source, err := normalizeSource(request.Source)
	if err != nil {
		return WorkspaceRequest{}, err
	}
	normalized := request.clone()
	normalized.Source = source
	return normalized, nil
}

func (request WorkspaceRequest) clone() WorkspaceRequest {
	clone := request
	clone.RequiredCapabilities = cloneCapabilities(request.RequiredCapabilities)
	clone.SecurityRequirements.AllowedEndpoints = cloneStrings(request.SecurityRequirements.AllowedEndpoints)
	return clone
}

func cloneCapabilities(capabilities []Capability) []Capability {
	return append([]Capability(nil), capabilities...)
}

func cloneStrings(values []string) []string {
	return append([]string(nil), values...)
}

func validCapabilities(capabilities []Capability) bool {
	for _, capability := range capabilities {
		if !isCapability(capability) {
			return false
		}
	}
	return true
}

func validWorkspaceEndpoints(endpoints []WorkspaceEndpoint) bool {
	seen := make(map[string]struct{}, len(endpoints))
	for _, endpoint := range endpoints {
		if endpoint.Kind == "" || strings.TrimSpace(endpoint.Kind) != endpoint.Kind || !isValidAbsoluteURI(endpoint.URI) || !validEndpointAccess(endpoint.Access) {
			return false
		}
		key := workspaceEndpointKey(endpoint)
		if _, duplicate := seen[key]; duplicate {
			return false
		}
		seen[key] = struct{}{}
	}
	return true
}

func validEndpointAccess(access []string) bool {
	if len(access) == 0 {
		return false
	}
	seen := make(map[string]struct{}, len(access))
	for _, value := range access {
		if value == "" || strings.TrimSpace(value) != value {
			return false
		}
		if _, duplicate := seen[value]; duplicate {
			return false
		}
		seen[value] = struct{}{}
	}
	return true
}

func requiresStableEndpoints(request WorkspaceRequest, handles ...WorkspaceHandle) bool {
	if hasCapability(request.requiredCapabilities(""), CapabilityStableEndpoint) {
		return true
	}
	for _, handle := range handles {
		if hasCapability(handle.ObservedCapabilities, CapabilityStableEndpoint) {
			return true
		}
	}
	return false
}

func hasCapability(capabilities []Capability, expected Capability) bool {
	for _, capability := range capabilities {
		if capability == expected {
			return true
		}
	}
	return false
}

func sameSecurityRequirements(left, right SecurityRequirements) bool {
	return left.Isolation == right.Isolation &&
		left.Network == right.Network &&
		sameStrings(left.AllowedEndpoints, right.AllowedEndpoints) &&
		left.Credentials == right.Credentials &&
		left.Data == right.Data
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy, rightCopy := cloneStrings(left), cloneStrings(right)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	for index := range leftCopy {
		if leftCopy[index] != rightCopy[index] {
			return false
		}
	}
	return true
}

func cloneWorkspaceEndpoints(endpoints []WorkspaceEndpoint) []WorkspaceEndpoint {
	cloned := make([]WorkspaceEndpoint, len(endpoints))
	for index, endpoint := range endpoints {
		cloned[index] = endpoint
		cloned[index].Access = cloneStrings(endpoint.Access)
	}
	return cloned
}

func cloneWorkspaceHandle(handle WorkspaceHandle) WorkspaceHandle {
	clone := handle
	clone.Endpoints = cloneWorkspaceEndpoints(handle.Endpoints)
	clone.ObservedCapabilities = cloneCapabilities(handle.ObservedCapabilities)
	return clone
}

func sameWorkspaceEndpoints(left, right []WorkspaceEndpoint) bool {
	if len(left) != len(right) {
		return false
	}
	remaining := make(map[string]int, len(right))
	for _, endpoint := range right {
		remaining[workspaceEndpointKey(endpoint)]++
	}
	for _, endpoint := range left {
		key := workspaceEndpointKey(endpoint)
		if remaining[key] == 0 {
			return false
		}
		remaining[key]--
	}
	return true
}

func workspaceEndpointKey(endpoint WorkspaceEndpoint) string {
	access := cloneStrings(endpoint.Access)
	sort.Strings(access)

	var builder strings.Builder
	writeWorkspaceEndpointKeyPart(&builder, endpoint.Kind)
	writeWorkspaceEndpointKeyPart(&builder, endpoint.URI)
	for _, value := range access {
		writeWorkspaceEndpointKeyPart(&builder, value)
	}
	return builder.String()
}

func writeWorkspaceEndpointKeyPart(builder *strings.Builder, value string) {
	builder.WriteString(strconv.Itoa(len(value)))
	builder.WriteByte(':')
	builder.WriteString(value)
}

func sameWorkspaceEndpoint(left, right WorkspaceEndpoint) bool {
	return left.Kind == right.Kind && left.URI == right.URI && sameStrings(left.Access, right.Access)
}

func supportsAll(available, required []Capability) bool {
	availableSet := make(map[Capability]struct{}, len(available))
	for _, capability := range available {
		availableSet[capability] = struct{}{}
	}
	for _, capability := range required {
		if _, ok := availableSet[capability]; !ok {
			return false
		}
	}
	return true
}

func sameCapabilities(left, right []Capability) bool {
	return supportsAll(left, right) && supportsAll(right, left)
}

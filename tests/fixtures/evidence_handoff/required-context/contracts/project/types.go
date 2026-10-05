// Package project defines the portable contracts for Project setup.
package project

import (
	"context"
	"encoding/json"
	"time"
)

// ExactSolutionRef identifies one immutable Solution package release.
type ExactSolutionRef struct {
	SolutionID     string `json:"solution_id"`
	Version        string `json:"version"`
	ArtifactDigest string `json:"artifact_digest"`
}

// ExactDeploymentDriverRef identifies the DeploymentDriver chosen by policy.
type ExactDeploymentDriverRef struct {
	DriverID       string `json:"driver_id"`
	Version        string `json:"version"`
	ArtifactDigest string `json:"artifact_digest"`
}

// ProjectDisplay is the user-maintained Project presentation data.
type ProjectDisplay struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

// Project is the long-lived boundary shared by workflows.
type Project struct {
	ProjectID           string                `json:"project_id"`
	OwnerScopeID        string                `json:"owner_scope_id"`
	Display             ProjectDisplay        `json:"display"`
	SolutionSetup       *ProjectSolutionSetup `json:"solution_setup,omitempty"`
	Revision            uint64                `json:"revision"`
	ConfigurationDigest string                `json:"configuration_digest"`
	ArchivedAt          *time.Time            `json:"archived_at,omitempty"`
	ArchivedBy          string                `json:"archived_by,omitempty"`
	CreatedBy           string                `json:"created_by"`
	CreatedAt           time.Time             `json:"created_at"`
}

// BeginProjectSetupRequest creates only the base Project and a Setup context.
// It never persists a partial SolutionSetup.
type BeginProjectSetupRequest struct {
	Display      ProjectDisplay   `json:"display"`
	OwnerScopeID string           `json:"owner_scope_id"`
	Solution     ExactSolutionRef `json:"solution"`
}

// ProjectParameters holds schema-normalized values. Values remain JSON so a
// Solution schema can express typed fields without making Project a schema owner.
type ProjectParameters map[string]json.RawMessage

// RepositoryAccess describes the allowed business use of a repository binding.
type RepositoryAccess string

const (
	RepositoryAccessPrimaryWrite  RepositoryAccess = "primary-write"
	RepositoryAccessSecondaryRead RepositoryAccess = "secondary-read"
)

// RepositoryDisplay is probe-produced, user-readable repository metadata.
type RepositoryDisplay struct {
	Name      string `json:"name"`
	OwnerPath string `json:"owner_path"`
}

// RepositoryCandidate contains only user-submitted Repository setup values.
// Provider type, default branch, and display metadata are authoritative probe output.
type RepositoryCandidate struct {
	BindingKey          string           `json:"binding_key"`
	RepositoryURI       string           `json:"repository_uri"`
	Access              RepositoryAccess `json:"access"`
	CredentialBindingID string           `json:"credential_binding_id,omitempty"`
}

// RepositoryBinding connects a Project to a repository for one business use.
type RepositoryBinding struct {
	BindingKey          string            `json:"binding_key"`
	RepositoryURI       string            `json:"repository_uri"`
	Access              RepositoryAccess  `json:"access"`
	CredentialBindingID string            `json:"credential_binding_id,omitempty"`
	ProviderType        string            `json:"provider_type,omitempty"`
	DefaultBranch       string            `json:"default_branch,omitempty"`
	Display             RepositoryDisplay `json:"display,omitempty"`
}

// EnvironmentUsage is a permitted workflow environment use.
type EnvironmentUsage string

const (
	EnvironmentUsagePreview     EnvironmentUsage = "preview"
	EnvironmentUsageObservation EnvironmentUsage = "observation"
)

// EnvironmentBinding is an environment fact resolved by the system or policy.
type EnvironmentBinding struct {
	EnvironmentID string             `json:"environment_id"`
	Usages        []EnvironmentUsage `json:"usages"`
}

// ExternalTargetDisplay is the user-maintained presentation data for one
// external deployment target.
type ExternalTargetDisplay struct {
	Name string `json:"name"`
}

// ExternalTargetCandidate carries only user-selectable target inputs.
type ExternalTargetCandidate struct {
	BindingKey          string                `json:"binding_key"`
	Display             ExternalTargetDisplay `json:"display"`
	TargetType          string                `json:"target_type"`
	Environment         string                `json:"environment"`
	EndpointURI         string                `json:"endpoint_uri"`
	CredentialBindingID string                `json:"credential_binding_id,omitempty"`
}

// ExternalTargetBinding is the adopted target, including system-resolved facts.
type ExternalTargetBinding struct {
	ExternalTargetCandidate
	DeploymentDriver          ExactDeploymentDriverRef `json:"deployment_driver"`
	TargetConfigurationDigest string                   `json:"target_configuration_digest"`
	ConcurrencyKey            string                   `json:"concurrency_key"`
}

// ResponsibilityBinding is the persisted authoritative team binding.
type ResponsibilityBinding struct {
	ResponsibilityID            string   `json:"responsibility_id"`
	CandidateAgentDefinitionIDs []string `json:"candidate_agent_definition_ids"`
	RequiredCapabilities        []string `json:"required_capabilities,omitempty"`
}

// SetupRequirement records whether a Solution responsibility slot must be
// selected during Project setup or is merely recommended.
type SetupRequirement string

const (
	SetupRequirementRequired    SetupRequirement = "required"
	SetupRequirementRecommended SetupRequirement = "recommended"
)

// ResponsibilitySelection carries only AgentDefinition choices a user may
// make. The evaluator binds it to an exact Solution slot internally; callers
// cannot select or name that slot.
type ResponsibilitySelection struct {
	CandidateAgentDefinitionIDs []string `json:"candidate_agent_definition_ids"`
}

// EvaluatedTeamBinding is evaluator-owned data that binds a selection to the
// exact immutable Solution slot that produced it. It is never a wire DTO.
type EvaluatedTeamBinding struct {
	SelectionIndex              int
	SlotKey                     string
	SetupRequirement            SetupRequirement
	ResponsibilityID            string
	CandidateAgentDefinitionIDs []string
	RequiredCapabilities        []string
}

// PolicyOverrides contains only authorized restrictions to the effective policy.
type PolicyOverrides map[string]json.RawMessage

// ProjectSolutionSetup is the atomically adopted Project configuration.
type ProjectSolutionSetup struct {
	Solution            ExactSolutionRef        `json:"solution"`
	ProjectParameters   ProjectParameters       `json:"project_parameters,omitempty"`
	Repositories        []RepositoryBinding     `json:"repositories,omitempty"`
	EnvironmentBindings []EnvironmentBinding    `json:"environment_bindings,omitempty"`
	ExternalTargets     []ExternalTargetBinding `json:"external_targets,omitempty"`
	TeamBindings        []ResponsibilityBinding `json:"team_bindings"`
	PolicyOverrides     PolicyOverrides         `json:"policy_overrides,omitempty"`
}

// ProjectSetupCandidate is the complete unsaved form submitted for evaluation.
// It is a transient DTO, not a Project state or persisted resource.
type ProjectSetupCandidate struct {
	ProjectID              string                    `json:"project_id"`
	Solution               ExactSolutionRef          `json:"solution"`
	ProjectParameters      ProjectParameters         `json:"project_parameters,omitempty"`
	Repositories           []RepositoryCandidate     `json:"repositories,omitempty"`
	ExternalTargets        []ExternalTargetCandidate `json:"external_targets,omitempty"`
	ResponsibilityBindings []ResponsibilitySelection `json:"responsibility_bindings"`
	PolicyOverrides        PolicyOverrides           `json:"policy_overrides,omitempty"`
}

// ProjectSetupReadiness is the only readiness result for a transient evaluation.
type ProjectSetupReadiness string

const (
	ProjectSetupReadinessReady   ProjectSetupReadiness = "ready"
	ProjectSetupReadinessBlocked ProjectSetupReadiness = "blocked"
)

// ProjectSetupCheckGroup partitions evaluation results into the five fixed groups.
type ProjectSetupCheckGroup string

const (
	ProjectSetupCheckSolutionPackage             ProjectSetupCheckGroup = "solution_package"
	ProjectSetupCheckRepositoryExternal          ProjectSetupCheckGroup = "repository_external"
	ProjectSetupCheckAgentSlots                  ProjectSetupCheckGroup = "agent_slots"
	ProjectSetupCheckRuntimeWorkspaceEnvironment ProjectSetupCheckGroup = "runtime_workspace_environment"
	ProjectSetupCheckAuthorizationGovernance     ProjectSetupCheckGroup = "authorization_governance"
)

// ProjectSetupCheckStatus is the result for one evaluation group.
type ProjectSetupCheckStatus string

const (
	ProjectSetupCheckPassed        ProjectSetupCheckStatus = "passed"
	ProjectSetupCheckWarning       ProjectSetupCheckStatus = "warning"
	ProjectSetupCheckBlocked       ProjectSetupCheckStatus = "blocked"
	ProjectSetupCheckNotApplicable ProjectSetupCheckStatus = "not_applicable"
)

// ProjectSetupCheck describes a fixed evaluation group without exposing secrets.
type ProjectSetupCheck struct {
	Group   ProjectSetupCheckGroup  `json:"group"`
	Status  ProjectSetupCheckStatus `json:"status"`
	Summary string                  `json:"summary"`
}

// FindingSubject identifies the candidate value or existing resource at issue.
type FindingSubject struct {
	Kind          string `json:"kind"`
	CandidatePath string `json:"candidate_path,omitempty"`
	ResourceID    string `json:"resource_id,omitempty"`
	Display       string `json:"display,omitempty"`
}

// FixActionType limits remediation links to platform-controlled actions.
type FixActionType string

const (
	FixActionFocusField FixActionType = "focus_field"
	FixActionOpenRoute  FixActionType = "open_route"
	FixActionRerunCheck FixActionType = "rerun_check"

	RouteAgentCenter            = "agent_center"
	RouteCredentialBindings     = "credential_bindings"
	RouteProjectSetup           = "project_setup"
	RerunProjectSetupEvaluation = "project_setup_evaluation"
)

// FixAction is an optional, controlled remediation action.
type FixAction struct {
	Type   FixActionType `json:"type"`
	Target string        `json:"target"`
}

// ProjectSetupFinding is a structured blocker or warning.
type ProjectSetupFinding struct {
	Code        string                 `json:"code"`
	Group       ProjectSetupCheckGroup `json:"group"`
	Subject     FindingSubject         `json:"subject"`
	Message     string                 `json:"message"`
	Remediation string                 `json:"remediation"`
	FixAction   *FixAction             `json:"fix_action,omitempty"`
}

// ProjectSetupNormalizedValues are the schema- and policy-normalized values
// that Platform Core authoritatively permits adoption to persist.
type ProjectSetupNormalizedValues struct {
	ProjectParameters ProjectParameters `json:"project_parameters,omitempty"`
	PolicyOverrides   PolicyOverrides   `json:"policy_overrides,omitempty"`
}

// ResolvedPackage explains a package resolved during transient evaluation.
type ResolvedPackage struct {
	PackageType        ResolvedPackageType       `json:"package_type"`
	PackageID          string                    `json:"package_id"`
	Version            string                    `json:"version"`
	ArtifactDigest     string                    `json:"artifact_digest"`
	InstallStatus      PackageInstallStatus      `json:"install_status"`
	VerificationStatus PackageVerificationStatus `json:"verification_status"`
}

// ResolvedPackageType is the closed discriminator for a resolved package fact.
type ResolvedPackageType string

const (
	ResolvedPackageTypeSolution  ResolvedPackageType = "solution"
	ResolvedPackageTypeContent   ResolvedPackageType = "content"
	ResolvedPackageTypeExtension ResolvedPackageType = "extension"
)

// PackageInstallStatus is the installation result included in evaluation output.
type PackageInstallStatus string

const (
	PackageInstallStatusActive   PackageInstallStatus = "active"
	PackageInstallStatusInactive PackageInstallStatus = "inactive"
)

// PackageVerificationStatus is the package check result included in evaluation output.
type PackageVerificationStatus string

const (
	PackageVerificationStatusPassed PackageVerificationStatus = "passed"
	PackageVerificationStatusFailed PackageVerificationStatus = "failed"
)

// ProjectSetupEvaluation is the exact transient evaluation response DTO. It
// deliberately excludes evaluator-only normalized and resolved adoption data.
type ProjectSetupEvaluation struct {
	ProjectID        string                `json:"project_id"`
	Solution         ExactSolutionRef      `json:"solution"`
	CandidateDigest  string                `json:"candidate_digest"`
	Readiness        ProjectSetupReadiness `json:"readiness"`
	Checks           []ProjectSetupCheck   `json:"checks"`
	Blockers         []ProjectSetupFinding `json:"blockers,omitempty"`
	Warnings         []ProjectSetupFinding `json:"warnings,omitempty"`
	ResolvedPackages []ResolvedPackage     `json:"resolved_packages,omitempty"`
	EvaluatedAt      time.Time             `json:"evaluated_at"`
}

// IsStaleFor reports whether this result belongs to another candidate revision.
func (evaluation ProjectSetupEvaluation) IsStaleFor(candidateDigest string) bool {
	return evaluation.CandidateDigest != candidateDigest
}

// ProjectSetupValidationResult is server-internal evaluator output used only by
// adoption. JSON deliberately omits every field so normalized and resolved
// values cannot become a client-authored evaluation wire contract.
type ProjectSetupValidationResult struct {
	ProjectSetupEvaluation `json:"-"`
	Repositories           []RepositoryBinding          `json:"-"`
	EnvironmentBindings    []EnvironmentBinding         `json:"-"`
	ExternalTargets        []ExternalTargetBinding      `json:"-"`
	TeamBindings           []EvaluatedTeamBinding       `json:"-"`
	NormalizedValues       ProjectSetupNormalizedValues `json:"-"`
}

// ProjectSetupValidator performs the current authoritative validation required
// by adoption. Implementations own authorization, probes, audit behavior, and
// the internal projection that must not be accepted from a client.
type ProjectSetupValidator interface {
	Evaluate(ctx context.Context, candidate ProjectSetupCandidate) (ProjectSetupValidationResult, error)
}

// AdoptProjectSolutionSetupRequest replaces the whole setup under revision CAS.
type AdoptProjectSolutionSetupRequest struct {
	ProjectID        string                `json:"project_id"`
	ExpectedRevision uint64                `json:"expected_revision"`
	Candidate        ProjectSetupCandidate `json:"candidate"`
	CandidateDigest  string                `json:"candidate_digest"`
	IdempotencyKey   string                `json:"idempotency_key"`
}

// SuccessorContext supplies the approved predecessor relationship to the
// ordinary workflow-create command. It never supplies a workflow identity.
type SuccessorContext struct {
	PredecessorWorkflowName  string `json:"predecessor_workflow_name"`
	ImpactAnalysisArtifactID string `json:"impact_analysis_artifact_id"`
	ApprovalDecisionID       string `json:"approval_decision_id"`
}

// CreateWorkflowFromProjectSetup is the single workflow creation path. The
// server creates the workflow identity after checking the Project CAS snapshot.
type CreateWorkflowFromProjectSetupRequest struct {
	ProjectID                   string            `json:"project_id"`
	ExpectedProjectRevision     uint64            `json:"expected_project_revision"`
	ExpectedConfigurationDigest string            `json:"expected_configuration_digest"`
	Goal                        WorkflowGoal      `json:"goal"`
	InputArtifactIDs            []string          `json:"input_artifact_ids,omitempty"`
	WorkflowInputs              ProjectParameters `json:"workflow_inputs,omitempty"`
	IdempotencyKey              string            `json:"idempotency_key"`
	SuccessorContext            *SuccessorContext `json:"successor_context,omitempty"`
}

// WorkflowGoal is the minimal user-authored intent from which the server
// creates an immutable Goal Artifact and a generated Workflow identity.
type WorkflowGoal struct {
	Title            string   `json:"title"`
	Description      string   `json:"description"`
	Context          string   `json:"context,omitempty"`
	KnownConstraints []string `json:"known_constraints,omitempty"`
}

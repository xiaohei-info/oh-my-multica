// Package admission publishes governance execution-admission contracts.
package admission

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
	"github.com/xiaohei-info/open-agent-cluster/contracts/kernel"
)

type ExecutionKind string

const (
	ExecutionKindComponentRun     ExecutionKind = "component-run"
	ExecutionKindDirectDeployment ExecutionKind = "direct-deployment"
	ExecutionKindChildWorkflow    ExecutionKind = "child-workflow"
	ExecutionKindAgentRun         ExecutionKind = "agent-run"
	ExecutionKindHumanRun         ExecutionKind = "human-run"
	ExecutionKindWorkspace        ExecutionKind = "workspace"
)

func (kind ExecutionKind) Valid() bool {
	switch kind {
	case ExecutionKindComponentRun, ExecutionKindDirectDeployment, ExecutionKindChildWorkflow, ExecutionKindAgentRun, ExecutionKindHumanRun, ExecutionKindWorkspace:
		return true
	default:
		return false
	}
}

type ResolvedExecutionKind string

const (
	ResolvedExecutionAgent     ResolvedExecutionKind = "agent"
	ResolvedExecutionComponent ResolvedExecutionKind = "component"
	ResolvedExecutionWorkspace ResolvedExecutionKind = "workspace"
	ResolvedExecutionHuman     ResolvedExecutionKind = "human"
	ResolvedExecutionExternal  ResolvedExecutionKind = "external"
)

type OwnerContextKind string

const (
	OwnerContextProject    OwnerContextKind = "project"
	OwnerContextWorkflow   OwnerContextKind = "workflow"
	OwnerContextWorkUnit   OwnerContextKind = "work-unit"
	OwnerContextDeployment OwnerContextKind = "deployment"
)

// ExecutionAdmissionRequest is the exact, authorized request to materialize one execution.
type ExecutionAdmissionRequest struct {
	RequestContext       requestcontext.RequestContext `json:"request_context"`
	ExecutionKind        ExecutionKind                 `json:"execution_kind"`
	Subject              kernel.SubjectKey             `json:"subject"`
	OwnerContext         OwnerContext                  `json:"owner_context"`
	ResolvedExecution    ResolvedExecution             `json:"resolved_execution"`
	ResourceRequirements ResourceRequirements          `json:"resource_requirements"`
	SecurityRequirements ExecutionSecurityRequirements `json:"security_requirements"`
	UsageContext         UsageContext                  `json:"usage_context"`
	IdempotencyKey       string                        `json:"idempotency_key"`
}

// OwnerContext is a tagged union. It records exactly one ownership boundary.
type OwnerContext struct {
	Kind       OwnerContextKind `json:"kind"`
	Project    *ProjectOwner    `json:"project,omitempty"`
	Workflow   *WorkflowOwner   `json:"workflow,omitempty"`
	WorkUnit   *WorkUnitOwner   `json:"work_unit,omitempty"`
	Deployment *DeploymentOwner `json:"deployment,omitempty"`
}

type ProjectOwner struct {
	ProjectID string `json:"project_id"`
}

type WorkflowOwner struct {
	ProjectID  string `json:"project_id"`
	WorkflowID string `json:"workflow_id"`
}

type WorkUnitOwner struct {
	ProjectID  string `json:"project_id"`
	WorkflowID string `json:"workflow_id"`
	WorkUnitID string `json:"work_unit_id"`
	Iteration  int64  `json:"iteration"`
}

type DeploymentOwner struct {
	ProjectID    string `json:"project_id"`
	DeploymentID string `json:"deployment_id"`
}

// ResolvedExecution is a tagged union. Exactly one execution implementation is selected.
type ResolvedExecution struct {
	Kind      ResolvedExecutionKind `json:"kind"`
	Agent     *AgentExecution       `json:"agent,omitempty"`
	Component *ComponentExecution   `json:"component,omitempty"`
	Workspace *WorkspaceExecution   `json:"workspace,omitempty"`
	Human     *HumanExecution       `json:"human,omitempty"`
	External  *ExternalExecution    `json:"external,omitempty"`
}

type AgentExecution struct {
	AgentDefinitionID string `json:"agent_definition_id"`
	RuntimeID         string `json:"runtime_id"`
}

type ComponentExecution struct {
	ComponentID string `json:"component_id"`
}

type WorkspaceExecution struct {
	WorkspaceID string `json:"workspace_id"`
}

type HumanExecution struct {
	PrincipalRequirementDigest string `json:"principal_requirement_digest"`
	OutputContractID           string `json:"output_contract_id"`
}

type ExternalExecution struct {
	ExternalExecutorID string `json:"external_executor_id"`
}

type ResourceRequirements struct {
	CPUMilliCores       int64    `json:"cpu_milli_cores"`
	MemoryBytes         int64    `json:"memory_bytes"`
	GPUCount            int64    `json:"gpu_count"`
	StorageBytes        int64    `json:"storage_bytes"`
	Architectures       []string `json:"architectures"`
	TopologyConstraints []string `json:"topology_constraints,omitempty"`
}

type NetworkMode string

const (
	NetworkModeDenied     NetworkMode = "denied"
	NetworkModeRestricted NetworkMode = "restricted"
	NetworkModeAllowed    NetworkMode = "allowed"
)

func (mode NetworkMode) Valid() bool {
	return mode == NetworkModeDenied || mode == NetworkModeRestricted || mode == NetworkModeAllowed
}

// RetentionPolicy records the evaluated retention requirement and its source policy provenance.
type RetentionPolicy struct {
	Requirement    string `json:"requirement"`
	PolicyID       string `json:"policy_id"`
	PolicyRevision int64  `json:"policy_revision"`
	PolicyDigest   string `json:"policy_digest"`
}

func (policy RetentionPolicy) Validate() error {
	if policy.Requirement == "" || policy.PolicyID == "" || policy.PolicyRevision < 1 || policy.PolicyDigest == "" {
		return fmt.Errorf("retention requirement and policy provenance are required")
	}
	return nil
}

// ExecutionSecurityRequirements is the authoritative security value contract for an execution.
type ExecutionSecurityRequirements struct {
	IsolationLevel       string          `json:"isolation_level"`
	NetworkMode          NetworkMode     `json:"network_mode"`
	AllowedEndpoints     []string        `json:"allowed_endpoints,omitempty"`
	CredentialBindingIDs []string        `json:"credential_binding_ids,omitempty"`
	DataClassification   string          `json:"data_classification"`
	DedicatedPlacement   *bool           `json:"dedicated_placement"`
	RetentionPolicy      RetentionPolicy `json:"retention_policy"`
}

type UsageContext struct {
	ObservedAt    time.Time `json:"observed_at"`
	SummaryDigest string    `json:"summary_digest"`
}

func (request ExecutionAdmissionRequest) Validate() error {
	if err := request.RequestContext.Validate(); err != nil {
		return fmt.Errorf("request context: %w", err)
	}
	if !request.ExecutionKind.Valid() || request.IdempotencyKey == "" {
		return fmt.Errorf("request context, execution kind, and idempotency key are required")
	}
	if err := request.Subject.Validate(); err != nil {
		return err
	}
	if err := request.OwnerContext.Validate(); err != nil {
		return err
	}
	if err := request.ResolvedExecution.Validate(); err != nil {
		return err
	}
	if !request.ResolvedExecution.supports(request.ExecutionKind) {
		return fmt.Errorf("execution kind %q is incompatible with resolved execution %q", request.ExecutionKind, request.ResolvedExecution.Kind)
	}
	if request.ResourceRequirements.CPUMilliCores < 0 || request.ResourceRequirements.MemoryBytes < 0 || request.ResourceRequirements.GPUCount < 0 || request.ResourceRequirements.StorageBytes < 0 {
		return fmt.Errorf("resource requirements cannot be negative")
	}
	if err := validateNonEmpty(request.ResourceRequirements.Architectures, "architecture"); err != nil {
		return err
	}
	if err := request.SecurityRequirements.Validate(); err != nil {
		return err
	}
	return request.UsageContext.Validate()
}

func (owner OwnerContext) Validate() error {
	variants := 0
	if owner.Project != nil {
		variants++
	}
	if owner.Workflow != nil {
		variants++
	}
	if owner.WorkUnit != nil {
		variants++
	}
	if owner.Deployment != nil {
		variants++
	}
	if variants != 1 {
		return fmt.Errorf("owner context must select exactly one variant")
	}

	switch owner.Kind {
	case OwnerContextProject:
		if owner.Project == nil || owner.Project.ProjectID == "" {
			return fmt.Errorf("project owner requires project identity")
		}
	case OwnerContextWorkflow:
		if owner.Workflow == nil || owner.Workflow.ProjectID == "" || owner.Workflow.WorkflowID == "" {
			return fmt.Errorf("workflow owner requires project and workflow identities")
		}
	case OwnerContextWorkUnit:
		if owner.WorkUnit == nil || owner.WorkUnit.ProjectID == "" || owner.WorkUnit.WorkflowID == "" || owner.WorkUnit.WorkUnitID == "" || owner.WorkUnit.Iteration < 1 {
			return fmt.Errorf("work unit owner requires project, workflow, work unit, and positive iteration")
		}
	case OwnerContextDeployment:
		if owner.Deployment == nil || owner.Deployment.ProjectID == "" || owner.Deployment.DeploymentID == "" {
			return fmt.Errorf("deployment owner requires project and deployment identities")
		}
	default:
		return fmt.Errorf("owner context kind is invalid")
	}
	return nil
}

func (owner *OwnerContext) UnmarshalJSON(data []byte) error {
	type wireOwnerContext OwnerContext
	var decoded wireOwnerContext
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	value := OwnerContext(decoded)
	if err := value.Validate(); err != nil {
		return err
	}
	*owner = value
	return nil
}

func (execution ResolvedExecution) Validate() error {
	variants := 0
	if execution.Agent != nil {
		variants++
	}
	if execution.Component != nil {
		variants++
	}
	if execution.Workspace != nil {
		variants++
	}
	if execution.Human != nil {
		variants++
	}
	if execution.External != nil {
		variants++
	}
	if variants != 1 {
		return fmt.Errorf("resolved execution must select exactly one implementation")
	}

	switch execution.Kind {
	case ResolvedExecutionAgent:
		if execution.Agent == nil || execution.Agent.AgentDefinitionID == "" || execution.Agent.RuntimeID == "" {
			return fmt.Errorf("agent execution requires agent definition and runtime")
		}
	case ResolvedExecutionComponent:
		if execution.Component == nil || execution.Component.ComponentID == "" {
			return fmt.Errorf("component execution requires component")
		}
	case ResolvedExecutionWorkspace:
		if execution.Workspace == nil || execution.Workspace.WorkspaceID == "" {
			return fmt.Errorf("workspace execution requires workspace")
		}
	case ResolvedExecutionHuman:
		if execution.Human == nil || execution.Human.PrincipalRequirementDigest == "" || execution.Human.OutputContractID == "" {
			return fmt.Errorf("human execution requires principal requirement and output contract")
		}
	case ResolvedExecutionExternal:
		if execution.External == nil || execution.External.ExternalExecutorID == "" {
			return fmt.Errorf("external execution requires executor")
		}
	default:
		return fmt.Errorf("resolved execution kind is invalid")
	}
	return nil
}

func (execution *ResolvedExecution) UnmarshalJSON(data []byte) error {
	type wireResolvedExecution ResolvedExecution
	var decoded wireResolvedExecution
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	value := ResolvedExecution(decoded)
	if err := value.Validate(); err != nil {
		return err
	}
	*execution = value
	return nil
}

func (execution ResolvedExecution) supports(kind ExecutionKind) bool {
	switch kind {
	case ExecutionKindAgentRun:
		return execution.Kind == ResolvedExecutionAgent
	case ExecutionKindComponentRun:
		return execution.Kind == ResolvedExecutionComponent
	case ExecutionKindWorkspace:
		return execution.Kind == ResolvedExecutionWorkspace
	case ExecutionKindHumanRun:
		return execution.Kind == ResolvedExecutionHuman
	case ExecutionKindDirectDeployment, ExecutionKindChildWorkflow:
		return execution.Kind == ResolvedExecutionExternal
	default:
		return false
	}
}

func (requirements ExecutionSecurityRequirements) Validate() error {
	if requirements.IsolationLevel == "" || !requirements.NetworkMode.Valid() || requirements.DataClassification == "" || requirements.DedicatedPlacement == nil {
		return fmt.Errorf("isolation level, network mode, data classification, and dedicated placement are required")
	}
	if err := validateNetworkEndpoints(requirements.NetworkMode, requirements.AllowedEndpoints); err != nil {
		return err
	}
	if err := validateNonEmpty(requirements.CredentialBindingIDs, "credential binding id"); err != nil {
		return err
	}
	return requirements.RetentionPolicy.Validate()
}

func validateNetworkEndpoints(mode NetworkMode, endpoints []string) error {
	switch mode {
	case NetworkModeDenied:
		if len(endpoints) != 0 {
			return fmt.Errorf("denied network mode cannot declare allowed endpoints")
		}
	case NetworkModeRestricted:
		if len(endpoints) == 0 {
			return fmt.Errorf("restricted network mode requires allowed endpoints")
		}
	}
	return validateNonEmpty(endpoints, "allowed endpoint")
}

func (context UsageContext) Validate() error {
	if context.ObservedAt.IsZero() || context.SummaryDigest == "" {
		return fmt.Errorf("usage observation time and summary digest are required")
	}
	return nil
}

func validateNonEmpty(values []string, kind string) error {
	for _, value := range values {
		if value == "" {
			return fmt.Errorf("%s is required", kind)
		}
	}
	return nil
}

type CheckStatus string

const (
	CheckStatusPass    CheckStatus = "pass"
	CheckStatusFail    CheckStatus = "fail"
	CheckStatusUnknown CheckStatus = "unknown"
)

func (status CheckStatus) Valid() bool {
	return status == CheckStatusPass || status == CheckStatusFail || status == CheckStatusUnknown
}

// ExecutionAdmissionFacts are transient facts from existing authoritative boundaries.
type ExecutionAdmissionFacts struct {
	Checks      []ExternalCheckFact `json:"checks"`
	EvaluatedAt time.Time           `json:"evaluated_at"`
}

// ExternalCheckFact is an authoritative input fact returned by an external port.
// Its evidence digest is required so the admission decision remains auditable.
type ExternalCheckFact struct {
	CheckKey       string      `json:"check_key"`
	Status         CheckStatus `json:"status"`
	ReasonCode     string      `json:"reason_code"`
	EvidenceDigest string      `json:"evidence_digest"`
	ObservedAt     time.Time   `json:"observed_at"`
	ExpiresAt      time.Time   `json:"expires_at"`
}

func (facts ExecutionAdmissionFacts) Validate() error {
	if facts.EvaluatedAt.IsZero() || len(facts.Checks) == 0 {
		return fmt.Errorf("fact evaluation time and checks are required")
	}
	keys := make(map[string]struct{}, len(facts.Checks))
	for _, check := range facts.Checks {
		if err := check.ValidateAt(facts.EvaluatedAt); err != nil {
			return err
		}
		if _, exists := keys[check.CheckKey]; exists {
			return fmt.Errorf("duplicate fact check key %q", check.CheckKey)
		}
		keys[check.CheckKey] = struct{}{}
	}
	return nil
}

func (check ExternalCheckFact) Validate() error {
	if check.EvidenceDigest == "" {
		return fmt.Errorf("external fact check %q requires an evidence digest", check.CheckKey)
	}
	return validateCheckFields(check.CheckKey, check.Status, check.ReasonCode, check.ObservedAt, check.ExpiresAt)
}

func (check ExternalCheckFact) ValidateAt(evaluatedAt time.Time) error {
	if err := check.Validate(); err != nil {
		return err
	}
	if check.ObservedAt.After(evaluatedAt) || !evaluatedAt.Before(check.ExpiresAt) {
		return fmt.Errorf("external fact check %q is outside its valid observation interval", check.CheckKey)
	}
	return nil
}

func validateCheckFields(checkKey string, status CheckStatus, reasonCode string, observedAt, expiresAt time.Time) error {
	if checkKey == "" || !status.Valid() || reasonCode == "" || observedAt.IsZero() || expiresAt.IsZero() {
		return fmt.Errorf("check key, status, reason code, observation time, and expiry are required")
	}
	if observedAt.After(expiresAt) {
		return fmt.Errorf("check %q expires before it was observed", checkKey)
	}
	return nil
}

type AdmissionEffect string

const (
	AdmissionEffectAdmit  AdmissionEffect = "admit"
	AdmissionEffectReject AdmissionEffect = "reject"
)

func (effect AdmissionEffect) Valid() bool {
	return effect == AdmissionEffectAdmit || effect == AdmissionEffectReject
}

// ExecutionAdmissionDecision is immutable rules and facts provenance for one request digest.
type ExecutionAdmissionDecision struct {
	ExecutionAdmissionDecisionID string           `json:"execution_admission_decision_id"`
	RequestDigest                string           `json:"request_digest"`
	Effect                       AdmissionEffect  `json:"effect"`
	ReasonCode                   string           `json:"reason_code"`
	EvaluatedChecks              []EvaluatedCheck `json:"evaluated_checks"`
	RequiredCheckKeys            []string         `json:"required_check_keys"`
	Constraints                  []Constraint     `json:"constraints,omitempty"`
	RulesDigest                  string           `json:"rules_digest"`
	FactsDigest                  string           `json:"facts_digest"`
	EvaluatedAt                  time.Time        `json:"evaluated_at"`
}

// EvaluatedCheck is an immutable check captured in an admission decision.
// It may omit external evidence when the check was evaluated internally.
type EvaluatedCheck struct {
	CheckKey       string      `json:"check_key"`
	Status         CheckStatus `json:"status"`
	ReasonCode     string      `json:"reason_code"`
	EvidenceDigest string      `json:"evidence_digest,omitempty"`
	ObservedAt     time.Time   `json:"observed_at"`
	ExpiresAt      time.Time   `json:"expires_at"`
}

func (check EvaluatedCheck) ValidateForDecision(evaluatedAt time.Time, effect AdmissionEffect) error {
	if err := validateCheckFields(check.CheckKey, check.Status, check.ReasonCode, check.ObservedAt, check.ExpiresAt); err != nil {
		return err
	}
	if check.ObservedAt.After(evaluatedAt) {
		return fmt.Errorf("evaluated check %q was observed after the decision evaluation time", check.CheckKey)
	}
	if effect == AdmissionEffectAdmit && !evaluatedAt.Before(check.ExpiresAt) {
		return fmt.Errorf("evaluated check %q is outside its valid observation interval", check.CheckKey)
	}
	return nil
}

type Constraint struct {
	ConstraintKey string `json:"constraint_key"`
	Value         string `json:"value"`
}

func (decision ExecutionAdmissionDecision) Validate() error {
	if decision.ExecutionAdmissionDecisionID == "" || decision.RequestDigest == "" || !decision.Effect.Valid() || decision.ReasonCode == "" || decision.RulesDigest == "" || decision.FactsDigest == "" || decision.EvaluatedAt.IsZero() {
		return fmt.Errorf("decision identity, request digest, effect, reason, rules digest, facts digest, and evaluation time are required")
	}
	if len(decision.EvaluatedChecks) == 0 || len(decision.RequiredCheckKeys) == 0 {
		return fmt.Errorf("evaluated checks and required check keys are required")
	}
	requiredChecks := make(map[string]struct{}, len(decision.RequiredCheckKeys))
	for _, key := range decision.RequiredCheckKeys {
		if key == "" {
			return fmt.Errorf("required check key is required")
		}
		if _, exists := requiredChecks[key]; exists {
			return fmt.Errorf("duplicate required check key %q", key)
		}
		requiredChecks[key] = struct{}{}
	}
	evaluatedChecks := make(map[string]CheckStatus, len(decision.EvaluatedChecks))
	for _, check := range decision.EvaluatedChecks {
		if err := check.ValidateForDecision(decision.EvaluatedAt, decision.Effect); err != nil {
			return err
		}
		if _, exists := evaluatedChecks[check.CheckKey]; exists {
			return fmt.Errorf("duplicate evaluated check key %q", check.CheckKey)
		}
		evaluatedChecks[check.CheckKey] = check.Status
	}
	if decision.Effect == AdmissionEffectAdmit {
		for requiredKey := range requiredChecks {
			if status, found := evaluatedChecks[requiredKey]; !found || status != CheckStatusPass {
				return fmt.Errorf("admit decision requires every required check to be present and pass")
			}
		}
	}
	for _, constraint := range decision.Constraints {
		if constraint.ConstraintKey == "" || constraint.Value == "" {
			return fmt.Errorf("constraint key and value are required")
		}
	}
	return nil
}

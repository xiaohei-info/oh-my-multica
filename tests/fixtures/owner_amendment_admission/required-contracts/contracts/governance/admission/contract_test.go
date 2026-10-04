package admission

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/principal"
	"github.com/xiaohei-info/open-agent-cluster/contracts/identity/requestcontext"
	"github.com/xiaohei-info/open-agent-cluster/contracts/kernel"
)

func TestExecutionAdmissionRequestValidateAcceptsResolvedAgentRun(t *testing.T) {
	if err := validRequest().Validate(); err != nil {
		t.Fatalf("validate request: %v", err)
	}
}

func TestExecutionAdmissionRequestValidateRejectsIncompleteAuthoritativeInputs(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*ExecutionAdmissionRequest)
	}{
		{"unknown execution kind", func(request *ExecutionAdmissionRequest) { request.ExecutionKind = "unknown" }},
		{"incomplete request context", func(request *ExecutionAdmissionRequest) { request.RequestContext.CausationID = "" }},
		{"missing subject digest", func(request *ExecutionAdmissionRequest) { request.Subject.Digest = "" }},
		{"missing owner", func(request *ExecutionAdmissionRequest) { request.OwnerContext = OwnerContext{} }},
		{"foreign resolved execution variant", func(request *ExecutionAdmissionRequest) {
			request.ResolvedExecution.Component = &ComponentExecution{ComponentID: "component-1"}
		}},
		{"incompatible execution kind", func(request *ExecutionAdmissionRequest) { request.ExecutionKind = ExecutionKindHumanRun }},
		{"negative resource requirement", func(request *ExecutionAdmissionRequest) { request.ResourceRequirements.MemoryBytes = -1 }},
		{"empty architecture", func(request *ExecutionAdmissionRequest) { request.ResourceRequirements.Architectures = []string{""} }},
		{"missing security context", func(request *ExecutionAdmissionRequest) { request.SecurityRequirements.IsolationLevel = "" }},
		{"missing dedicated placement", func(request *ExecutionAdmissionRequest) { request.SecurityRequirements.DedicatedPlacement = nil }},
		{"missing usage context", func(request *ExecutionAdmissionRequest) { request.UsageContext.SummaryDigest = "" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := validRequest()
			test.mutate(&request)
			if err := request.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
}

func TestOwnerContextValidateRequiresOneWellFormedVariant(t *testing.T) {
	if err := validRequest().OwnerContext.Validate(); err != nil {
		t.Fatalf("validate owner: %v", err)
	}
	for _, owner := range []OwnerContext{
		{Kind: OwnerContextProject, Project: &ProjectOwner{ProjectID: "project-1"}},
		{Kind: OwnerContextWorkflow, Workflow: &WorkflowOwner{ProjectID: "project-1", WorkflowID: "workflow-1"}},
		{Kind: OwnerContextDeployment, Deployment: &DeploymentOwner{ProjectID: "project-1", DeploymentID: "deployment-1"}},
	} {
		if err := owner.Validate(); err != nil {
			t.Fatalf("validate owner %q: %v", owner.Kind, err)
		}
	}
	owner := validRequest().OwnerContext
	owner.Deployment = &DeploymentOwner{ProjectID: "project-1", DeploymentID: "deployment-1"}
	if err := owner.Validate(); err == nil {
		t.Fatal("Validate() accepted multiple owner variants")
	}
	owner = OwnerContext{Kind: OwnerContextWorkUnit, WorkUnit: &WorkUnitOwner{ProjectID: "project-1", WorkflowID: "workflow-1", WorkUnitID: "work-unit-1"}}
	if err := owner.Validate(); err == nil {
		t.Fatal("Validate() accepted incomplete work unit owner")
	}
	owner = OwnerContext{Kind: "unknown", Project: &ProjectOwner{ProjectID: "project-1"}}
	if err := owner.Validate(); err == nil {
		t.Fatal("Validate() accepted unknown owner context kind")
	}
}

func TestResolvedExecutionValidateSupportsEachPublishedVariant(t *testing.T) {
	tests := []ResolvedExecution{
		{Kind: ResolvedExecutionAgent, Agent: &AgentExecution{AgentDefinitionID: "agent-1", RuntimeID: "runtime-1"}},
		{Kind: ResolvedExecutionComponent, Component: &ComponentExecution{ComponentID: "component-1"}},
		{Kind: ResolvedExecutionWorkspace, Workspace: &WorkspaceExecution{WorkspaceID: "workspace-1"}},
		{Kind: ResolvedExecutionHuman, Human: &HumanExecution{PrincipalRequirementDigest: "sha256:requirement", OutputContractID: "output-1"}},
		{Kind: ResolvedExecutionExternal, External: &ExternalExecution{ExternalExecutorID: "external-1"}},
	}
	for _, execution := range tests {
		if err := execution.Validate(); err != nil {
			t.Fatalf("validate %q execution: %v", execution.Kind, err)
		}
	}
	if err := (ResolvedExecution{Kind: ResolvedExecutionAgent, Agent: &AgentExecution{AgentDefinitionID: "agent-1"}}).Validate(); err == nil {
		t.Fatal("Validate() accepted agent without runtime")
	}
	if err := (ResolvedExecution{Kind: ResolvedExecutionHuman, Human: &HumanExecution{PrincipalRequirementDigest: "sha256:requirement"}}).Validate(); err == nil {
		t.Fatal("Validate() accepted human without output contract")
	}
	if err := (ResolvedExecution{Kind: ResolvedExecutionComponent, Agent: &AgentExecution{AgentDefinitionID: "agent-1", RuntimeID: "runtime-1"}}).Validate(); err == nil {
		t.Fatal("Validate() accepted a mismatched execution variant")
	}
}

func TestExecutionAdmissionRequestValidateAcceptsEveryLegalExecutionMapping(t *testing.T) {
	tests := []struct {
		kind     ExecutionKind
		resolved ResolvedExecution
	}{
		{ExecutionKindComponentRun, ResolvedExecution{Kind: ResolvedExecutionComponent, Component: &ComponentExecution{ComponentID: "component-1"}}},
		{ExecutionKindDirectDeployment, ResolvedExecution{Kind: ResolvedExecutionExternal, External: &ExternalExecution{ExternalExecutorID: "deployment-driver-1"}}},
		{ExecutionKindChildWorkflow, ResolvedExecution{Kind: ResolvedExecutionExternal, External: &ExternalExecution{ExternalExecutorID: "workflow-engine-1"}}},
		{ExecutionKindAgentRun, ResolvedExecution{Kind: ResolvedExecutionAgent, Agent: &AgentExecution{AgentDefinitionID: "agent-1", RuntimeID: "runtime-1"}}},
		{ExecutionKindHumanRun, ResolvedExecution{Kind: ResolvedExecutionHuman, Human: &HumanExecution{PrincipalRequirementDigest: "sha256:requirement", OutputContractID: "output-1"}}},
		{ExecutionKindWorkspace, ResolvedExecution{Kind: ResolvedExecutionWorkspace, Workspace: &WorkspaceExecution{WorkspaceID: "workspace-1"}}},
	}
	for _, test := range tests {
		request := validRequest()
		request.ExecutionKind = test.kind
		request.ResolvedExecution = test.resolved
		if err := request.Validate(); err != nil {
			t.Fatalf("validate %q mapping: %v", test.kind, err)
		}
	}
}

func TestSecurityAndUsageContextValidationRejectsIncompleteValues(t *testing.T) {
	security := validRequest().SecurityRequirements
	security.NetworkMode = NetworkModeRestricted
	security.AllowedEndpoints = nil
	if err := security.Validate(); err == nil {
		t.Fatal("Validate() accepted restricted networking without allowed endpoints")
	}
	security = validRequest().SecurityRequirements
	security.NetworkMode = NetworkModeDenied
	security.AllowedEndpoints = []string{"https://example.test"}
	if err := security.Validate(); err == nil {
		t.Fatal("Validate() accepted endpoints for denied networking")
	}
	security = validRequest().SecurityRequirements
	security.CredentialBindingIDs = nil
	if err := security.Validate(); err != nil {
		t.Fatalf("Validate() rejected credential-free execution: %v", err)
	}
	security = validRequest().SecurityRequirements
	security.RetentionPolicy.PolicyDigest = ""
	if err := security.Validate(); err == nil {
		t.Fatal("Validate() accepted incomplete retention policy provenance")
	}
	usage := validRequest().UsageContext
	usage.ObservedAt = time.Time{}
	if err := usage.Validate(); err == nil {
		t.Fatal("Validate() accepted missing usage observation time")
	}
}

func TestExecutionAdmissionFactsValidateFailsClosedForExternalInputs(t *testing.T) {
	if err := (ExecutionAdmissionFacts{}).Validate(); err == nil {
		t.Fatal("Validate() accepted empty facts")
	}
	facts := validFacts()
	if err := facts.Validate(); err != nil {
		t.Fatalf("validate facts: %v", err)
	}
	facts.Checks = append(facts.Checks, facts.Checks[0])
	if err := facts.Validate(); err == nil {
		t.Fatal("Validate() accepted duplicate check")
	}
	facts = validFacts()
	facts.Checks[0].ExpiresAt = facts.EvaluatedAt.Add(-time.Second)
	if err := facts.Validate(); err == nil {
		t.Fatal("Validate() accepted stale check")
	}
	facts = validFacts()
	facts.Checks[0].EvidenceDigest = ""
	if err := facts.Validate(); err == nil {
		t.Fatal("Validate() accepted an evidence-less external pass fact")
	}
	facts = validFacts()
	facts.Checks[0].ObservedAt = facts.EvaluatedAt.Add(time.Second)
	if err := facts.Validate(); err == nil {
		t.Fatal("Validate() accepted a future observation")
	}
}

func TestExecutionAdmissionDecisionValidateFailsClosed(t *testing.T) {
	if err := validDecision().Validate(); err != nil {
		t.Fatalf("validate decision: %v", err)
	}
	tests := []struct {
		name   string
		mutate func(*ExecutionAdmissionDecision)
	}{
		{"missing evaluated checks", func(decision *ExecutionAdmissionDecision) { decision.EvaluatedChecks = nil }},
		{"missing required checks", func(decision *ExecutionAdmissionDecision) { decision.RequiredCheckKeys = nil }},
		{"missing required check", func(decision *ExecutionAdmissionDecision) {
			decision.RequiredCheckKeys = append(decision.RequiredCheckKeys, "credential")
		}},
		{"failed required check", func(decision *ExecutionAdmissionDecision) { decision.EvaluatedChecks[0].Status = CheckStatusFail }},
		{"unknown required check", func(decision *ExecutionAdmissionDecision) { decision.EvaluatedChecks[0].Status = CheckStatusUnknown }},
		{"duplicate evaluated check", func(decision *ExecutionAdmissionDecision) {
			decision.EvaluatedChecks = append(decision.EvaluatedChecks, decision.EvaluatedChecks[0])
		}},
		{"duplicate required check", func(decision *ExecutionAdmissionDecision) {
			decision.RequiredCheckKeys = append(decision.RequiredCheckKeys, "capacity")
		}},
		{"empty required check", func(decision *ExecutionAdmissionDecision) { decision.RequiredCheckKeys[0] = "" }},
		{"malformed constraint", func(decision *ExecutionAdmissionDecision) { decision.Constraints = []Constraint{{Value: "restricted"}} }},
		{"expired evaluated check", func(decision *ExecutionAdmissionDecision) {
			decision.EvaluatedChecks[0].ExpiresAt = decision.EvaluatedAt
		}},
		{"future rejected observation", func(decision *ExecutionAdmissionDecision) {
			decision.Effect = AdmissionEffectReject
			decision.EvaluatedChecks[0].ObservedAt = decision.EvaluatedAt.Add(time.Second)
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := validDecision()
			test.mutate(&decision)
			if err := decision.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
	decision := validDecision()
	decision.Effect = AdmissionEffectReject
	decision.EvaluatedChecks[0].Status = CheckStatusFail
	if err := decision.Validate(); err != nil {
		t.Fatalf("validate rejection: %v", err)
	}
}

func TestExecutionAdmissionRejectRetainsDiagnosticFacts(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*ExecutionAdmissionDecision)
	}{
		{"stale fact", func(decision *ExecutionAdmissionDecision) {
			decision.Effect = AdmissionEffectReject
			decision.EvaluatedChecks[0].Status = CheckStatusFail
			decision.EvaluatedChecks[0].ObservedAt = decision.EvaluatedAt.Add(-time.Minute)
			decision.EvaluatedChecks[0].ExpiresAt = decision.EvaluatedAt
		}},
		{"timeout unknown", func(decision *ExecutionAdmissionDecision) {
			decision.Effect = AdmissionEffectReject
			decision.EvaluatedChecks[0].Status = CheckStatusUnknown
			decision.EvaluatedChecks[0].ReasonCode = "external-check-timeout"
			decision.EvaluatedChecks[0].EvidenceDigest = ""
		}},
		{"evidence-less internal check", func(decision *ExecutionAdmissionDecision) {
			decision.Effect = AdmissionEffectReject
			decision.EvaluatedChecks[0].Status = CheckStatusFail
			decision.EvaluatedChecks[0].EvidenceDigest = ""
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := validDecision()
			test.mutate(&decision)
			if err := decision.Validate(); err != nil {
				t.Fatalf("Validate() rejected diagnostic evidence: %v", err)
			}
		})
	}
}

func TestAdmissionContractsPublishSnakeCaseWireFieldsAndRejectMismatchedVariants(t *testing.T) {
	encoded, err := json.Marshal(validRequest())
	if err != nil {
		t.Fatalf("Marshal() returned %v", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatalf("Unmarshal() returned %v", err)
	}
	for _, key := range []string{"request_context", "execution_kind", "owner_context", "resolved_execution", "security_requirements"} {
		if _, found := fields[key]; !found {
			t.Fatalf("request JSON has no %q: %s", key, encoded)
		}
	}
	if _, found := fields["ExecutionKind"]; found {
		t.Fatalf("request JSON exposed Go field names: %s", encoded)
	}
	var decodedRequest ExecutionAdmissionRequest
	if err := json.Unmarshal(encoded, &decodedRequest); err != nil {
		t.Fatalf("Unmarshal request returned %v", err)
	}
	if !reflect.DeepEqual(decodedRequest, validRequest()) {
		t.Fatalf("request round trip = %#v, want %#v", decodedRequest, validRequest())
	}

	var owner OwnerContext
	if err := json.Unmarshal([]byte(`{"kind":"project","workflow":{"project_id":"project-1","workflow_id":"workflow-1"}}`), &owner); err == nil {
		t.Fatal("Unmarshal() accepted a mismatched owner variant")
	}
	var execution ResolvedExecution
	if err := json.Unmarshal([]byte(`{"kind":"agent","component":{"component_id":"component-1"}}`), &execution); err == nil {
		t.Fatal("Unmarshal() accepted a mismatched execution variant")
	}
}

func TestPublishedEnumsRejectUnknownValues(t *testing.T) {
	if !ExecutionKindAgentRun.Valid() || !CheckStatusPass.Valid() || !AdmissionEffectAdmit.Valid() {
		t.Fatal("published enum values are invalid")
	}
	if ExecutionKind("unknown").Valid() || CheckStatus("invalid").Valid() || AdmissionEffect("unknown").Valid() {
		t.Fatal("unknown enum value is valid")
	}
}

func validRequest() ExecutionAdmissionRequest {
	return ExecutionAdmissionRequest{
		RequestContext: validRequestContext(), ExecutionKind: ExecutionKindAgentRun,
		Subject:              kernel.SubjectKey{Kind: "agent-definition", ID: "agent-1", Digest: "sha256:agent"},
		OwnerContext:         OwnerContext{Kind: OwnerContextWorkUnit, WorkUnit: &WorkUnitOwner{ProjectID: "project-1", WorkflowID: "workflow-1", WorkUnitID: "work-unit-1", Iteration: 1}},
		ResolvedExecution:    ResolvedExecution{Kind: ResolvedExecutionAgent, Agent: &AgentExecution{AgentDefinitionID: "agent-1", RuntimeID: "runtime-1"}},
		ResourceRequirements: ResourceRequirements{CPUMilliCores: 500, MemoryBytes: 512 * 1024 * 1024, Architectures: []string{"amd64"}},
		SecurityRequirements: ExecutionSecurityRequirements{IsolationLevel: "container", NetworkMode: NetworkModeRestricted, AllowedEndpoints: []string{"cluster-internal"}, DataClassification: "internal", DedicatedPlacement: boolPointer(true), RetentionPolicy: RetentionPolicy{Requirement: "retain-for-30-days", PolicyID: "retention-policy-1", PolicyRevision: 1, PolicyDigest: "sha256:retention"}},
		UsageContext:         UsageContext{ObservedAt: evaluationTime(), SummaryDigest: "sha256:usage"}, IdempotencyKey: "agent-run-request-1",
	}
}

func validRequestContext() requestcontext.RequestContext {
	return requestcontext.RequestContext{
		PrincipalIdentity: principal.PrincipalIdentity{PrincipalID: "principal-1"},
		ScopeIdentity:     requestcontext.ScopeIdentity{ScopeID: "scope-1"},
		RequestID:         "request-1",
		CausationID:       "causation-1",
		CorrelationID:     "correlation-1",
		Authentication: requestcontext.AuthenticationContext{
			AuthenticationMethodID: "method-1",
			AuthenticationDriver:   "driver-1",
			ExternalIdentityLinkID: "link-1",
			AuthenticationStrength: "mfa",
			AuthenticatedAt:        evaluationTime(),
		},
		Source:                "web",
		PolicyContextRevision: 1,
		PolicyContextDigest:   "sha256:policy-context",
	}
}

func validFacts() ExecutionAdmissionFacts {
	observedAt := evaluationTime()
	return ExecutionAdmissionFacts{EvaluatedAt: observedAt, Checks: []ExternalCheckFact{{CheckKey: "capacity", Status: CheckStatusPass, ReasonCode: "capacity-available", EvidenceDigest: "sha256:evidence", ObservedAt: observedAt, ExpiresAt: observedAt.Add(time.Minute)}}}
}

func validDecision() ExecutionAdmissionDecision {
	check := validFacts().Checks[0]
	return ExecutionAdmissionDecision{ExecutionAdmissionDecisionID: "admission-decision-1", RequestDigest: "sha256:request", Effect: AdmissionEffectAdmit, ReasonCode: "admission-passed", EvaluatedChecks: []EvaluatedCheck{{CheckKey: check.CheckKey, Status: check.Status, ReasonCode: check.ReasonCode, EvidenceDigest: check.EvidenceDigest, ObservedAt: check.ObservedAt, ExpiresAt: check.ExpiresAt}}, RequiredCheckKeys: []string{"capacity"}, RulesDigest: "sha256:rules", FactsDigest: "sha256:facts", EvaluatedAt: evaluationTime()}
}

func evaluationTime() time.Time { return time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC) }

func boolPointer(value bool) *bool { return &value }

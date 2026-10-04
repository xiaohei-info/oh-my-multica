package project

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

const validTestDigest = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
const otherTestDigest = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

func TestBeginProjectSetupRequestRequiresExplicitProjectIdentity(t *testing.T) {
	request := validBeginProjectSetupRequest()

	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}

	request.Solution.ArtifactDigest = ""
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() succeeded without an exact Solution digest")
	}
}

func TestProjectSetupEvaluationRequiresFiveGroupsAndRejectsStaleCandidate(t *testing.T) {
	candidate := validCandidate()
	evaluation := validEvaluation(candidate)

	if err := evaluation.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	if evaluation.IsStaleFor(evaluation.CandidateDigest) {
		t.Fatal("IsStaleFor() marked the evaluated candidate as stale")
	}
	if !evaluation.IsStaleFor("another-candidate") {
		t.Fatal("IsStaleFor() accepted a different candidate digest")
	}

	evaluation.Checks = evaluation.Checks[:4]
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted an evaluation without all five check groups")
	}
}

func TestAdoptProjectSolutionSetupUsesAuthoritativeValidationAndCAS(t *testing.T) {
	candidate := validCandidate()
	current := Project{
		ProjectID:           "project-1",
		OwnerScopeID:        "scope-1",
		Display:             ProjectDisplay{Name: "Payments", Description: "Payment delivery"},
		Revision:            7,
		ConfigurationDigest: "sha256:old-config",
		CreatedBy:           "principal-1",
		CreatedAt:           time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
	}
	request := AdoptProjectSolutionSetupRequest{
		ProjectID:        current.ProjectID,
		ExpectedRevision: current.Revision,
		Candidate:        candidate,
		CandidateDigest:  validEvaluation(candidate).CandidateDigest,
		IdempotencyKey:   "adopt-project-1-v7",
	}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), current, request, staticValidator{evaluation: validEvaluation(candidate)})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if adopted.Revision != 8 {
		t.Fatalf("Revision = %d, want 8", adopted.Revision)
	}
	if adopted.ConfigurationDigest != adopted.SolutionSetup.ConfigurationDigest() {
		t.Fatalf("ConfigurationDigest = %q, want %q", adopted.ConfigurationDigest, adopted.SolutionSetup.ConfigurationDigest())
	}
	if adopted.SolutionSetup == nil || adopted.SolutionSetup.Solution != candidate.Solution {
		t.Fatalf("SolutionSetup = %#v, want adopted candidate", adopted.SolutionSetup)
	}
	if current.SolutionSetup != nil || current.Revision != 7 {
		t.Fatalf("current project was mutated: %#v", current)
	}

	request.ExpectedRevision = 6
	_, err = AdoptProjectSolutionSetup(context.Background(), current, request, staticValidator{evaluation: validEvaluation(candidate)})
	if err == nil {
		t.Fatal("AdoptProjectSolutionSetup() accepted a stale revision")
	}
	if current.SolutionSetup != nil || current.Revision != 7 {
		t.Fatalf("stale revision mutated current project: %#v", current)
	}
}

func TestCreateWorkflowFromProjectSetupUsesSuccessorContext(t *testing.T) {
	request := CreateWorkflowFromProjectSetupRequest{
		ProjectID:                   "project-1",
		ExpectedProjectRevision:     8,
		ExpectedConfigurationDigest: validTestDigest,
		Goal:                        WorkflowGoal{Title: "Successor delivery", Description: "Deliver approved successor workflow"},
		InputArtifactIDs:            []string{"artifact-goal-1", "artifact-impact-1"},
		IdempotencyKey:              "create-successor-project-1-v8",
		SuccessorContext: &SuccessorContext{
			PredecessorWorkflowName:  "workflow-predecessor",
			ImpactAnalysisArtifactID: "artifact-impact-1",
			ApprovalDecisionID:       "decision-1",
		},
	}

	if err := request.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}

	request.SuccessorContext.ApprovalDecisionID = ""
	if err := request.Validate(); err == nil {
		t.Fatal("Validate() accepted a successor without an approval decision")
	}
}

type staticValidator struct {
	evaluation ProjectSetupValidationResult
}

func (validator staticValidator) Evaluate(_ context.Context, _ ProjectSetupCandidate) (ProjectSetupValidationResult, error) {
	return validator.evaluation, nil
}

func validBeginProjectSetupRequest() BeginProjectSetupRequest {
	return BeginProjectSetupRequest{
		Display:      ProjectDisplay{Name: "Payments", Description: "Payment delivery"},
		OwnerScopeID: "scope-1",
		Solution: ExactSolutionRef{
			SolutionID:     "software-delivery",
			Version:        "1.0.0",
			ArtifactDigest: validTestDigest,
		},
	}
}

func validCandidate() ProjectSetupCandidate {
	return ProjectSetupCandidate{
		ProjectID: "project-1",
		Solution:  validBeginProjectSetupRequest().Solution,
		Repositories: []RepositoryCandidate{{
			BindingKey:    "primary-repository",
			RepositoryURI: "https://github.com/example/payments.git",
			Access:        RepositoryAccessPrimaryWrite,
		}},
		ResponsibilityBindings: []ResponsibilitySelection{{
			CandidateAgentDefinitionIDs: []string{"agent-definition-developer"},
		}},
	}
}

func validEvaluation(candidate ProjectSetupCandidate) ProjectSetupValidationResult {
	evaluation := ProjectSetupValidationResult{
		ProjectSetupEvaluation: ProjectSetupEvaluation{
			ProjectID:       candidate.ProjectID,
			Solution:        candidate.Solution,
			CandidateDigest: validTestDigest,
			Readiness:       ProjectSetupReadinessReady,
			Checks: []ProjectSetupCheck{
				{Group: ProjectSetupCheckSolutionPackage, Status: ProjectSetupCheckPassed, Summary: "方案与组件校验通过"},
				{Group: ProjectSetupCheckRepositoryExternal, Status: ProjectSetupCheckPassed, Summary: "仓库与外部系统校验通过"},
				{Group: ProjectSetupCheckAgentSlots, Status: ProjectSetupCheckPassed, Summary: "岗位候选校验通过"},
				{Group: ProjectSetupCheckRuntimeWorkspaceEnvironment, Status: ProjectSetupCheckPassed, Summary: "运行时、工作区与环境校验通过"},
				{Group: ProjectSetupCheckAuthorizationGovernance, Status: ProjectSetupCheckPassed, Summary: "授权与治理校验通过"},
			},
			EvaluatedAt: time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC),
		},
		NormalizedValues: ProjectSetupNormalizedValues{
			ProjectParameters: cloneProjectParameters(candidate.ProjectParameters),
			PolicyOverrides:   clonePolicyOverrides(candidate.PolicyOverrides),
		},
	}
	for _, repository := range candidate.Repositories {
		evaluation.Repositories = append(evaluation.Repositories, RepositoryBinding{
			BindingKey:          repository.BindingKey,
			RepositoryURI:       repository.RepositoryURI,
			Access:              repository.Access,
			CredentialBindingID: repository.CredentialBindingID,
			ProviderType:        "github",
			DefaultBranch:       "main",
			Display:             RepositoryDisplay{Name: "payments", OwnerPath: "example/payments"},
		})
	}
	evaluation.TeamBindings = []EvaluatedTeamBinding{{SelectionIndex: 0, SlotKey: "developer", SetupRequirement: SetupRequirementRequired, ResponsibilityID: "developer", CandidateAgentDefinitionIDs: append([]string(nil), candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs...), RequiredCapabilities: []string{"software-development"}}}
	return evaluation
}

func TestProjectSetupEvaluationDoesNotSerializeAdoptionProjection(t *testing.T) {
	result := validEvaluation(validCandidate())
	encoded, err := json.Marshal(result.ProjectSetupEvaluation)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	for _, required := range []string{"project_id", "solution", "candidate_digest", "readiness", "checks", "evaluated_at"} {
		if !strings.Contains(string(encoded), required) {
			t.Fatalf("evaluation JSON omitted %q: %s", required, encoded)
		}
	}
	for _, forbidden := range []string{"repositories", "environment_bindings", "external_targets", "team_bindings", "normalized_values"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatalf("validation result JSON leaked %q: %s", forbidden, encoded)
		}
	}
	encoded, err = json.Marshal(result)
	if err != nil || string(encoded) != "{}" {
		t.Fatalf("internal validation result serialized as %s, %v", encoded, err)
	}
}

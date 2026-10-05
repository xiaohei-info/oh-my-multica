package project

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestAdoptionAcceptsProbeNormalizedRepositoryURI(t *testing.T) {
	candidate := validCandidate()
	candidate.Repositories[0].RepositoryURI = "https://github.com/example/payments.git/"
	evaluation := validEvaluation(candidate)
	evaluation.Repositories[0].RepositoryURI = "https://github.com/example/payments.git"
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "normalized-uri"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if got := adopted.SolutionSetup.Repositories[0].RepositoryURI; got != "https://github.com/example/payments.git" {
		t.Fatalf("RepositoryURI = %q", got)
	}
}

func TestAdoptionAcceptsAuthoritativeProbeNormalizedURIs(t *testing.T) {
	candidate := validCandidate()
	candidate.Repositories[0].RepositoryURI = "https://EXAMPLE.test/repository.git/"
	candidate.ExternalTargets = []ExternalTargetCandidate{{
		BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/",
	}}
	evaluation := validEvaluation(candidate)
	evaluation.Repositories[0].RepositoryURI = "https://example.test/repository.git"
	evaluation.ExternalTargets = []ExternalTargetBinding{{
		ExternalTargetCandidate: ExternalTargetCandidate{
			BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test",
		},
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "authoritative-uri"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if got := adopted.SolutionSetup.Repositories[0].RepositoryURI; got != "https://example.test/repository.git" {
		t.Fatalf("RepositoryURI = %q", got)
	}
	if got := adopted.SolutionSetup.ExternalTargets[0].EndpointURI; got != "https://cluster.example.test/" {
		t.Fatalf("EndpointURI = %q", got)
	}
}

func TestAdoptionPersistsEvaluatorAuthoritativeTeamBindings(t *testing.T) {
	candidate := validCandidate()
	evaluation := validEvaluation(candidate)
	evaluation.TeamBindings[0] = EvaluatedTeamBinding{
		SelectionIndex:              0,
		SlotKey:                     "solution-developer-slot",
		ResponsibilityID:            "solution-developer-slot",
		SetupRequirement:            SetupRequirementRequired,
		CandidateAgentDefinitionIDs: []string{"agent-definition-developer"},
		RequiredCapabilities:        []string{"software-development", "repository-write"},
	}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "authoritative-team"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	binding := adopted.SolutionSetup.TeamBindings[0]
	if binding.ResponsibilityID != "solution-developer-slot" {
		t.Fatalf("ResponsibilityID = %q", binding.ResponsibilityID)
	}
	if got := binding.RequiredCapabilities; len(got) != 2 || got[0] != "repository-write" || got[1] != "software-development" {
		t.Fatalf("RequiredCapabilities = %#v", got)
	}
}

func TestAdoptionRejectsNonAuthoritativeTeamBindings(t *testing.T) {
	candidate := validCandidate()
	evaluation := validEvaluation(candidate)
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "team-mismatch"}

	evaluation.TeamBindings[0].CandidateAgentDefinitionIDs = []string{"invented-agent"}
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); !errors.Is(err, ErrEvaluationMismatch) {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v, want %v", err, ErrEvaluationMismatch)
	}

	evaluation = validEvaluation(candidate)
	evaluation.TeamBindings[0].RequiredCapabilities = nil
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() rejected a team binding with no baseline capabilities: %v", err)
	}
}

func TestEvaluationAllowsEmptyRecommendedSelectionButNotRequiredSelection(t *testing.T) {
	candidate := validCandidate()
	candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs = nil
	evaluation := validEvaluation(candidate)
	evaluation.TeamBindings[0].SetupRequirement = SetupRequirementRecommended
	if err := evaluation.ValidateForCandidate(candidate); err != nil {
		t.Fatalf("ValidateForCandidate() rejected an empty recommended selection: %v", err)
	}
	evaluation.TeamBindings[0].SetupRequirement = SetupRequirementRequired
	if err := evaluation.ValidateForCandidate(candidate); err == nil {
		t.Fatal("ValidateForCandidate() accepted an empty required selection")
	}
}

func TestTeamBindingsUseEvaluatorOwnedSelectionProjection(t *testing.T) {
	candidate := validCandidate()
	candidate.ResponsibilityBindings = append(candidate.ResponsibilityBindings, ResponsibilitySelection{
		CandidateAgentDefinitionIDs: []string{"agent-definition-reviewer"},
	})
	evaluation := validEvaluation(candidate)
	evaluation.TeamBindings = []EvaluatedTeamBinding{
		{SelectionIndex: 1, SlotKey: "reviewer", SetupRequirement: SetupRequirementRequired, ResponsibilityID: "reviewer", CandidateAgentDefinitionIDs: []string{"agent-definition-reviewer"}, RequiredCapabilities: []string{"review"}},
		{SelectionIndex: 0, SlotKey: "developer", SetupRequirement: SetupRequirementRequired, ResponsibilityID: "developer", CandidateAgentDefinitionIDs: []string{"agent-definition-developer"}, RequiredCapabilities: []string{"software-development"}},
	}
	if err := evaluation.ValidateForCandidate(candidate); err != nil {
		t.Fatalf("ValidateForCandidate() rejected order-independent team bindings: %v", err)
	}

	evaluation.TeamBindings[0].SelectionIndex = 0
	if err := evaluation.ValidateForCandidate(candidate); err == nil {
		t.Fatal("ValidateForCandidate() accepted two bindings for one evaluator projection index")
	}
}

func TestBlockedEvaluationAllowsEmptyRequiredSlotSelection(t *testing.T) {
	candidate := validCandidate()
	candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs = nil
	evaluation := validEvaluation(candidate)
	evaluation.Readiness = ProjectSetupReadinessBlocked
	evaluation.Checks[2].Status = ProjectSetupCheckBlocked
	evaluation.TeamBindings[0].CandidateAgentDefinitionIDs = nil
	evaluation.Blockers = []ProjectSetupFinding{{
		Code: "agent_slot.required_missing", Group: ProjectSetupCheckAgentSlots,
		Subject: FindingSubject{Kind: "agent_slot", CandidatePath: "responsibility_bindings[0]"},
		Message: "Required developer slot has no candidate", Remediation: "Select a compatible AgentDefinition",
	}}
	if err := evaluation.ValidateForCandidate(candidate); err != nil {
		t.Fatalf("ValidateForCandidate() rejected the blocked required-slot representation: %v", err)
	}
	evaluation.Blockers[0].Subject.CandidatePath = "responsibility_bindings[1]"
	if err := evaluation.ValidateForCandidate(candidate); err == nil {
		t.Fatal("ValidateForCandidate() accepted an empty required slot without its correlated blocker")
	}
	evaluation.Blockers[0].Subject.CandidatePath = "responsibility_bindings[0]"
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "blocked-required-slot"}
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); !errors.Is(err, ErrCandidateNotReady) {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v, want %v", err, ErrCandidateNotReady)
	}
}

func TestAdoptionCanonicalizesAuthoritativeProbeURIsBeforeDigesting(t *testing.T) {
	candidate := validCandidate()
	candidate.Repositories[0].RepositoryURI = "https://example.test/repository.git"
	candidate.ExternalTargets = []ExternalTargetCandidate{{
		BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/api",
	}}
	evaluation := validEvaluation(candidate)
	evaluation.Repositories[0].RepositoryURI = "HTTPS://EXAMPLE.test:443/repository.git/"
	evaluation.ExternalTargets = []ExternalTargetBinding{{
		ExternalTargetCandidate: ExternalTargetCandidate{
			BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "HTTPS://CLUSTER.example.test:443/api",
		},
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "canonical-probe-output"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if got := adopted.SolutionSetup.Repositories[0].RepositoryURI; got != candidate.Repositories[0].RepositoryURI {
		t.Fatalf("RepositoryURI = %q, want %q", got, candidate.Repositories[0].RepositoryURI)
	}
	if got := adopted.SolutionSetup.ExternalTargets[0].EndpointURI; got != candidate.ExternalTargets[0].EndpointURI {
		t.Fatalf("EndpointURI = %q, want %q", got, candidate.ExternalTargets[0].EndpointURI)
	}
	if got, want := adopted.ConfigurationDigest, adopted.SolutionSetup.ConfigurationDigest(); got != want {
		t.Fatalf("ConfigurationDigest = %q, want %q", got, want)
	}
}

func TestConfigurationDigestUsesPersistedURIRepresentation(t *testing.T) {
	canonical := ProjectSolutionSetup{
		Solution: validBeginProjectSetupRequest().Solution,
		Repositories: []RepositoryBinding{{
			BindingKey: "primary", RepositoryURI: "https://example.test/repository.git", Access: RepositoryAccessPrimaryWrite,
		}},
		ExternalTargets: []ExternalTargetBinding{{
			ExternalTargetCandidate:   ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/api"},
			DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
			TargetConfigurationDigest: validTestDigest,
			ConcurrencyKey:            "staging",
		}},
		TeamBindings: []ResponsibilityBinding{{
			ResponsibilityID: "developer", CandidateAgentDefinitionIDs: []string{"agent-definition-developer"}, RequiredCapabilities: []string{"software-development"},
		}},
	}
	equivalent := canonical
	equivalent.Repositories = append([]RepositoryBinding(nil), canonical.Repositories...)
	equivalent.Repositories[0].RepositoryURI = "HTTPS://EXAMPLE.test:443/repository.git/"
	equivalent.ExternalTargets = cloneExternalTargetBindings(canonical.ExternalTargets)
	equivalent.ExternalTargets[0].EndpointURI = "HTTPS://CLUSTER.example.test:443/api"

	if got, want := equivalent.ConfigurationDigest(), canonical.ConfigurationDigest(); got != want {
		t.Fatalf("ConfigurationDigest() = %q, want %q for URI-equivalent setup", got, want)
	}
}

func TestExternalTargetUsesNestedDisplayShapeAndDigestExcludesProvenance(t *testing.T) {
	target := ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test"}
	encoded, err := json.Marshal(target)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	if strings.Contains(string(encoded), "display_name") || !strings.Contains(string(encoded), `"display":{"name":"Staging"}`) {
		t.Fatalf("ExternalTargetCandidate JSON = %s", encoded)
	}
	setup := ProjectSolutionSetup{ExternalTargets: []ExternalTargetBinding{{ExternalTargetCandidate: target}}}
	encoded, err = json.Marshal(setup)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	if strings.Contains(string(encoded), "last_modified_by") {
		t.Fatalf("ProjectSolutionSetup JSON includes provenance metadata: %s", encoded)
	}
}

func TestEvaluationRejectsUnknownResolvedSemanticValues(t *testing.T) {
	evaluation := validEvaluation(validCandidate())
	evaluation.EnvironmentBindings = []EnvironmentBinding{{EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsage("promotion")}}}
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted promotion as a workflow environment usage")
	}

	evaluation = validEvaluation(validCandidate())
	evaluation.ResolvedPackages = []ResolvedPackage{{
		PackageType: "solution", PackageID: "software-delivery", Version: "1.0.0", ArtifactDigest: "sha256:solution",
		InstallStatus: PackageInstallStatus("invented"), VerificationStatus: PackageVerificationStatusPassed,
	}}
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted an unknown package install status")
	}
}

func TestFixActionsRejectUnsafeTargets(t *testing.T) {
	tests := []struct {
		name   string
		action FixAction
		valid  bool
	}{
		{"field path", FixAction{Type: FixActionFocusField, Target: "repositories[0].credential_binding_id"}, true},
		{"invalid field path", FixAction{Type: FixActionFocusField, Target: "repositories/0"}, false},
		{"rootless punctuation", FixAction{Type: FixActionFocusField, Target: "..."}, false},
		{"empty path index", FixAction{Type: FixActionFocusField, Target: "repositories[].access"}, false},
		{"non-numeric path index", FixAction{Type: FixActionFocusField, Target: "repositories[primary].access"}, false},
		{"unmatched path bracket", FixAction{Type: FixActionFocusField, Target: "repositories[0.access"}, false},
		{"unknown candidate field", FixAction{Type: FixActionFocusField, Target: "repositories[0].provider_type"}, false},
		{"script field path", FixAction{Type: FixActionFocusField, Target: "javascript:alert(1)"}, false},
		{"known route", FixAction{Type: FixActionOpenRoute, Target: RouteCredentialBindings}, true},
		{"url route", FixAction{Type: FixActionOpenRoute, Target: "javascript:alert(1)"}, false},
		{"rerun setup check", FixAction{Type: FixActionRerunCheck, Target: RerunProjectSetupEvaluation}, true},
		{"unknown rerun", FixAction{Type: FixActionRerunCheck, Target: "shell:go test"}, false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := test.action.Validate()
			if test.valid && err != nil {
				t.Fatalf("Validate() error = %v", err)
			}
			if !test.valid && err == nil {
				t.Fatal("Validate() accepted unsafe target")
			}
		})
	}
}

func TestProbeNormalizationAndCandidateBoundariesFailClosed(t *testing.T) {
	normalized, err := NormalizeProbeURI("HTTPS://EXAMPLE.test:443/a%2Fb/")
	if err != nil || normalized != "https://example.test/a%2Fb/" {
		t.Fatalf("NormalizeProbeURI() = %q, %v", normalized, err)
	}
	if _, err := NormalizeProbeURI("https://example.test/path?token=x"); err == nil {
		t.Fatal("NormalizeProbeURI() accepted a query")
	}
	candidate := validCandidate()
	if candidatePathExists(candidate, "repositories[999].access") {
		t.Fatal("candidatePathExists() accepted an out-of-range path")
	}
	archivedAt := candidateTime()
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: validEvaluation(candidate).CandidateDigest, IdempotencyKey: "archived"}
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1, ArchivedAt: &archivedAt}, request, staticValidator{evaluation: validEvaluation(candidate)}); err != ErrProjectArchived {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
}

func TestCandidatePathAndReadyPackageBoundaries(t *testing.T) {
	candidate := validCandidate()
	candidate.ExternalTargets = []ExternalTargetCandidate{{
		BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test",
	}}
	for _, path := range []string{"project_id", "solution.version", "repositories[0]", "repositories[0].access", "external_targets", "external_targets[0].display.name", "responsibility_bindings[0].candidate_agent_definition_ids"} {
		if !candidatePathExists(candidate, path) && path != "external_targets" {
			t.Fatalf("candidatePathExists(%q) = false", path)
		}
	}
	for _, path := range []string{"repositories[].access", "repositories[a].access", "repositories[0].unknown", "repositories[0].access.extra", "external_targets[0].display_name", "responsibility_bindings[0].slot_key", "responsibility_bindings[0].responsibility_id", "responsibility_bindings[0].required_capabilities"} {
		if validCandidatePath(path) {
			t.Fatalf("validCandidatePath(%q) = true", path)
		}
	}
	evaluation := validEvaluation(candidate)
	evaluation.ResolvedPackages = []ResolvedPackage{{PackageType: "solution", PackageID: "p", Version: "1", ArtifactDigest: "sha256:p", InstallStatus: PackageInstallStatusInactive, VerificationStatus: PackageVerificationStatusFailed}}
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted an unusable package in a ready evaluation")
	}
}

func candidateTime() time.Time { return time.Date(2026, time.July, 29, 0, 0, 0, 0, time.UTC) }

func TestRepositoryAndTargetURIsUseSeparateIdentityRules(t *testing.T) {
	repositoryCases := []struct {
		input string
		want  string
	}{
		{"https://EXAMPLE.test/a%7Eb/", "https://example.test/a~b"},
		{"https://EXAMPLE.test/%2e%2e/repository.git", "https://example.test/%2E%2E/repository.git"},
		{"ssh://git@EXAMPLE.test/example/payments.git", "ssh://git@example.test/example/payments.git"},
		{"git@EXAMPLE.test:example/payments.git", "ssh://git@example.test/~/example/payments.git"},
	}
	for _, test := range repositoryCases {
		t.Run(test.input, func(t *testing.T) {
			got, err := NormalizeRepositoryURI(test.input)
			if err != nil || got != test.want {
				t.Fatalf("NormalizeRepositoryURI() = %q, %v; want %q", got, err, test.want)
			}
		})
	}

	target, err := NormalizeTargetURI("HTTPS://[2001:DB8::1]:443/a%7Eb")
	if err != nil || target != "https://[2001:db8::1]/a~b" {
		t.Fatalf("NormalizeTargetURI() = %q, %v", target, err)
	}
	if second, err := NormalizeTargetURI(target); err != nil || second != target {
		t.Fatalf("NormalizeTargetURI() is not reentrant: %q, %v", second, err)
	}

	candidate := ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/api/"}
	binding := ExternalTargetBinding{
		ExternalTargetCandidate:   candidate,
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}
	binding.EndpointURI = "https://cluster.example.test/api"
	if err := matchingExternalTargetBindings([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{binding}); err == nil {
		t.Fatal("matchingExternalTargetBindings() accepted distinct target paths")
	}
}

func TestURIIdentityPreservesEncodedDotSegmentsOnTheWire(t *testing.T) {
	requestURIs := make(chan string, 2)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requestURIs <- request.RequestURI
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	raw := server.URL + "/%2e%2e/admin"
	normalized, err := NormalizeTargetURI(raw)
	if err != nil || normalized != server.URL+"/%2E%2E/admin" {
		t.Fatalf("NormalizeTargetURI() = %q, %v", normalized, err)
	}
	for _, target := range []string{raw, normalized} {
		response, err := http.Get(target)
		if err != nil {
			t.Fatalf("GET %q error = %v", target, err)
		}
		response.Body.Close()
	}
	first, second := <-requestURIs, <-requestURIs
	if first == "/../admin" || second == "/../admin" || !strings.EqualFold(first, second) {
		t.Fatalf("wire RequestURIs = %q, %q", first, second)
	}
}

func TestBlockedEvaluationAllowsOnlyExplainedPartialProbeOutput(t *testing.T) {
	candidate := validCandidate()
	evaluation := validEvaluation(candidate)
	evaluation.Readiness = ProjectSetupReadinessBlocked
	evaluation.Repositories = nil
	evaluation.Checks[1].Status = ProjectSetupCheckBlocked
	evaluation.Blockers = []ProjectSetupFinding{{
		Code: "repository.probe_unknown", Group: ProjectSetupCheckRepositoryExternal,
		Subject: FindingSubject{Kind: "repository", CandidatePath: "repositories[0]"},
		Message: "Repository probe did not complete", Remediation: "Retry the repository probe",
	}}
	if err := evaluation.ValidateForCandidate(candidate); err != nil {
		t.Fatalf("ValidateForCandidate() error = %v", err)
	}

	evaluation.Blockers[0].Subject.CandidatePath = "responsibility_bindings[0]"
	if err := evaluation.ValidateForCandidate(candidate); err == nil {
		t.Fatal("ValidateForCandidate() accepted unexplained missing repository output")
	}
}

func TestBlockedEvaluationAllowsOnlyExplainedPartialTargetOutput(t *testing.T) {
	candidate := validCandidate()
	candidate.ExternalTargets = []ExternalTargetCandidate{{
		BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/api",
	}}
	evaluation := validEvaluation(candidate)
	evaluation.Readiness = ProjectSetupReadinessBlocked
	evaluation.ExternalTargets = nil
	evaluation.Checks[1].Status = ProjectSetupCheckBlocked
	evaluation.Blockers = []ProjectSetupFinding{{
		Code: "external_target.probe_unknown", Group: ProjectSetupCheckRepositoryExternal,
		Subject: FindingSubject{Kind: "external_target", CandidatePath: "external_targets[0]"},
		Message: "Target probe did not complete", Remediation: "Retry the target probe",
	}}
	if err := evaluation.ValidateForCandidate(candidate); err != nil {
		t.Fatalf("ValidateForCandidate() error = %v", err)
	}

	evaluation.Blockers = nil
	if err := evaluation.ValidateForCandidate(candidate); err == nil {
		t.Fatal("ValidateForCandidate() accepted unexplained missing target output")
	}
}

func TestAdoptionPersistsOnlyAuthoritativeNormalizedValues(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"name": []byte(`"  payments  "`)}
	candidate.PolicyOverrides = PolicyOverrides{"network": []byte(`"restricted"`)}
	evaluation := validEvaluation(candidate)
	evaluation.NormalizedValues = ProjectSetupNormalizedValues{
		ProjectParameters: ProjectParameters{"name": []byte(`"payments"`)},
		PolicyOverrides:   PolicyOverrides{"network": []byte(`"isolated"`)},
	}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "normalized-values"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if got := string(adopted.SolutionSetup.ProjectParameters["name"]); got != `"payments"` {
		t.Fatalf("ProjectParameters[name] = %q", got)
	}
	if got := string(adopted.SolutionSetup.PolicyOverrides["network"]); got != `"isolated"` {
		t.Fatalf("PolicyOverrides[network] = %q", got)
	}
}

func TestAdoptionIsolatedFromValidatorCandidateMutation(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"name": []byte(`"payments"`)}
	evaluation := validEvaluation(candidate)
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "alias-isolation"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, mutatingValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs[0] == "" {
		t.Fatal("validator mutated the caller candidate")
	}
	if adopted.SolutionSetup.TeamBindings[0].CandidateAgentDefinitionIDs[0] == "" {
		t.Fatal("validator mutation reached the adopted setup")
	}
}

func TestAdoptionRejectsRevisionExhaustionBeforeEvaluation(t *testing.T) {
	candidate := validCandidate()
	evaluation := validEvaluation(candidate)
	validator := &countingValidator{evaluation: evaluation}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: math.MaxUint64, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "revision-exhaustion"}

	_, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: math.MaxUint64}, request, validator)
	if !errors.Is(err, ErrRevisionExhausted) {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v, want %v", err, ErrRevisionExhausted)
	}
	if validator.calls != 0 {
		t.Fatalf("validator calls = %d, want 0", validator.calls)
	}
}

func TestCandidatePathsRejectOverflowingIndexes(t *testing.T) {
	candidate := validCandidate()
	path := "responsibility_bindings[9223372036854775808]"
	if validCandidatePath(path) || candidatePathExists(candidate, path) {
		t.Fatalf("overflowing candidate path %q was accepted", path)
	}
}

func TestURIIdentityRejectsUnsafeOrMalformedForms(t *testing.T) {
	for _, raw := range []string{
		"ftp://example.test/repository.git",
		"https://user@example.test/repository.git",
		"ssh://git:secret@example.test/repository.git",
		"example.test:repository.git",
		"git@example.test:",
	} {
		t.Run(raw, func(t *testing.T) {
			if _, err := NormalizeRepositoryURI(raw); err == nil {
				t.Fatal("NormalizeRepositoryURI() accepted an unsafe form")
			}
		})
	}
	for _, raw := range []string{
		"ftp://cluster.example.test/api",
		"https://user@cluster.example.test/api",
		"https://cluster.example.test/api?token=secret",
	} {
		t.Run(raw, func(t *testing.T) {
			if _, err := NormalizeTargetURI(raw); err == nil {
				t.Fatal("NormalizeTargetURI() accepted an unsafe form")
			}
		})
	}
}

func TestURIIdentityNormalizesPortsAndSCPLikeIPv6(t *testing.T) {
	target, err := NormalizeTargetURI("https://[2001:DB8::1]:8443/api")
	if err != nil || target != "https://[2001:db8::1]:8443/api" {
		t.Fatalf("NormalizeTargetURI() = %q, %v", target, err)
	}
	repository, err := NormalizeRepositoryURI("git@[2001:DB8::1]:example/payments.git/")
	if err != nil || repository != "ssh://git@[2001:db8::1]/~/example/payments.git" {
		t.Fatalf("NormalizeRepositoryURI() = %q, %v", repository, err)
	}
	if _, err := NormalizeRepositoryURI("ssh://git@example.test/"); err == nil {
		t.Fatal("NormalizeRepositoryURI() accepted a repository root")
	}
}

func TestBlockedTargetSubsetRejectsSubstitutedOrExtraOutput(t *testing.T) {
	candidate := ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test/api"}
	binding := ExternalTargetBinding{
		ExternalTargetCandidate:   candidate,
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}
	if err := matchingExternalTargetBindingSubset([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{binding}, nil, nil); err != nil {
		t.Fatalf("matchingExternalTargetBindingSubset() error = %v", err)
	}

	forged := binding
	forged.EndpointURI = "https://forged.example.test/api"
	if err := matchingExternalTargetBindingSubset([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{forged}, nil, nil); err == nil {
		t.Fatal("matchingExternalTargetBindingSubset() accepted a substituted target")
	}
	if err := matchingExternalTargetBindingSubset([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{binding, binding}, nil, nil); err == nil {
		t.Fatal("matchingExternalTargetBindingSubset() accepted extra target output")
	}
}

func TestValidationResultRequiresValidAuthoritativeNormalizedValues(t *testing.T) {
	evaluation := validEvaluation(validCandidate())
	evaluation.NormalizedValues.ProjectParameters = ProjectParameters{"broken": []byte(`{`)}
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted malformed normalized values")
	}
}

func TestRepositoryURIPreservesGitPathModeAndRejectsAmbiguousSCPAuthority(t *testing.T) {
	homeRelative, err := NormalizeRepositoryURI("git@example.test:example/payments.git")
	if err != nil || homeRelative != "ssh://git@example.test/~/example/payments.git" {
		t.Fatalf("NormalizeRepositoryURI() = %q, %v", homeRelative, err)
	}
	absolute, err := NormalizeRepositoryURI("git@example.test:/example/payments.git")
	if err != nil || absolute != "ssh://git@example.test/example/payments.git" {
		t.Fatalf("NormalizeRepositoryURI() = %q, %v", absolute, err)
	}
	if homeRelative == absolute {
		t.Fatal("NormalizeRepositoryURI() collapsed home-relative and absolute Git paths")
	}
	if canonical, err := NormalizeRepositoryURI("ssh://git@example.test/~/example/payments.git"); err != nil || canonical != homeRelative {
		t.Fatalf("NormalizeRepositoryURI() = %q, %v; want %q", canonical, err, homeRelative)
	}
	for _, raw := range []string{"git@example.test/path:example.git", "git@@example.test:example.git", "git @example.test:example.git"} {
		if _, err := NormalizeRepositoryURI(raw); err == nil {
			t.Fatalf("NormalizeRepositoryURI() accepted ambiguous SCP-like authority %q", raw)
		}
	}
}

func TestTargetURIRejectsScopedIPv6(t *testing.T) {
	if _, err := NormalizeTargetURI("https://[fe80::1%25ETH0]/api"); err == nil {
		t.Fatal("NormalizeTargetURI() accepted scoped IPv6 without a reentrant representation")
	}
}

func TestBlockedEvaluationRequiresOwningProbeBlocker(t *testing.T) {
	candidate := validCandidate()
	tests := []struct {
		name    string
		group   ProjectSetupCheckGroup
		code    string
		kind    string
		blocked int
	}{
		{"unrelated group", ProjectSetupCheckAgentSlots, "agent_slot.capability_missing", "agent", 2},
		{"wrong code namespace", ProjectSetupCheckRepositoryExternal, "external_target.probe_unknown", "repository", 1},
		{"wrong subject kind", ProjectSetupCheckRepositoryExternal, "repository.probe_unknown", "external_target", 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			evaluation := validEvaluation(candidate)
			evaluation.Readiness = ProjectSetupReadinessBlocked
			evaluation.Repositories = nil
			evaluation.Checks[test.blocked].Status = ProjectSetupCheckBlocked
			evaluation.Blockers = []ProjectSetupFinding{{
				Code: test.code, Group: test.group,
				Subject: FindingSubject{Kind: test.kind, CandidatePath: "repositories[0].repository_uri"},
				Message: "Probe did not complete", Remediation: "Correct the probe",
			}}
			if err := evaluation.ValidateForCandidate(candidate); err == nil {
				t.Fatal("ValidateForCandidate() accepted unrelated blocker for missing repository output")
			}
		})
	}
}

func TestNormalizedValuesMustCorrespondToCandidateAndBeUnambiguous(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = nil
	candidate.PolicyOverrides = nil
	evaluation := validEvaluation(candidate)
	evaluation.NormalizedValues = ProjectSetupNormalizedValues{
		ProjectParameters: ProjectParameters{"foreign": []byte(`true`)},
		PolicyOverrides:   PolicyOverrides{"privilege": []byte(`"elevated"`)},
	}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "foreign-normalized-values"}
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); err == nil {
		t.Fatal("AdoptProjectSolutionSetup() accepted normalized values not sourced by candidate")
	}

	evaluation = validEvaluation(validCandidate())
	evaluation.NormalizedValues.ProjectParameters = ProjectParameters{"role": []byte(`{"role":"user","role":"admin"}`)}
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted duplicate JSON object members")
	}
}

func TestAdoptionPersistsCanonicalNormalizedJSON(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"configuration": []byte(` { "mode" : "safe" } `)}
	candidate.PolicyOverrides = nil
	evaluation := validEvaluation(candidate)
	evaluation.NormalizedValues = ProjectSetupNormalizedValues{
		ProjectParameters: cloneProjectParameters(candidate.ProjectParameters),
	}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "canonical-normalized-json"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if got := string(adopted.SolutionSetup.ProjectParameters["configuration"]); got != `{"mode":"safe"}` {
		t.Fatalf("persisted normalized JSON = %q", got)
	}
}

func TestNormalizedValueCorrespondenceRejectsSameLengthWrongKeys(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"project_name": []byte(`"payments"`)}
	candidate.PolicyOverrides = PolicyOverrides{"network": []byte(`"isolated"`)}
	evaluation := validEvaluation(candidate)
	evaluation.NormalizedValues = ProjectSetupNormalizedValues{
		ProjectParameters: ProjectParameters{"foreign_name": []byte(`"payments"`)},
		PolicyOverrides:   PolicyOverrides{"foreign_network": []byte(`"isolated"`)},
	}
	if err := evaluation.ValidateForCandidate(candidate); !errors.Is(err, ErrEvaluationMismatch) {
		t.Fatalf("ValidateForCandidate() error = %v, want %v", err, ErrEvaluationMismatch)
	}
}

func TestNormalizedValuesCanonicalizeNestedJSONAndRejectDuplicatePolicyMembers(t *testing.T) {
	evaluation := validEvaluation(validCandidate())
	evaluation.NormalizedValues = ProjectSetupNormalizedValues{
		ProjectParameters: ProjectParameters{"values": []byte(`[true,{"count":1}]`)},
		PolicyOverrides:   PolicyOverrides{"network": []byte(` { "mode" : "isolated" } `)},
	}
	canonical, err := evaluation.NormalizedValues.Canonicalize()
	if err != nil {
		t.Fatalf("Canonicalize() error = %v", err)
	}
	if got := string(canonical.PolicyOverrides["network"]); got != `{"mode":"isolated"}` {
		t.Fatalf("canonical policy JSON = %q", got)
	}

	evaluation.NormalizedValues.PolicyOverrides["network"] = []byte(`{"mode":"safe","mode":"unsafe"}`)
	if err := evaluation.Validate(); err == nil {
		t.Fatal("Validate() accepted duplicate policy object members")
	}
}

type mutatingValidator struct {
	evaluation ProjectSetupValidationResult
}

func (validator mutatingValidator) Evaluate(_ context.Context, candidate ProjectSetupCandidate) (ProjectSetupValidationResult, error) {
	candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs[0] = ""
	candidate.ProjectParameters["name"][1] = 'X'
	return validator.evaluation, nil
}

type countingValidator struct {
	evaluation ProjectSetupValidationResult
	calls      int
}

func (validator *countingValidator) Evaluate(_ context.Context, _ ProjectSetupCandidate) (ProjectSetupValidationResult, error) {
	validator.calls++
	return validator.evaluation, nil
}

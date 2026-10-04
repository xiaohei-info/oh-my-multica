package project

import (
	"context"
	"reflect"
	"testing"
)

func TestCanonicalizeOrdersUnorderedValues(t *testing.T) {
	first := validCandidate()
	first.Repositories = []RepositoryCandidate{{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
	}}
	first.ResponsibilityBindings[0].CandidateAgentDefinitionIDs = []string{"agent-b", "agent-a"}
	first.ResponsibilityBindings = append(first.ResponsibilityBindings, ResponsibilitySelection{CandidateAgentDefinitionIDs: []string{"agent-reviewer"}})
	first.ProjectParameters = ProjectParameters{"limits": []byte(` { "retries" : 3 } `)}
	second := first
	second.ResponsibilityBindings = append([]ResponsibilitySelection(nil), first.ResponsibilityBindings...)
	second.ResponsibilityBindings[0].CandidateAgentDefinitionIDs = []string{"agent-a", "agent-b"}
	second.ProjectParameters = ProjectParameters{"limits": []byte(`{"retries":3}`)}

	firstCanonical, err := first.Canonicalize()
	if err != nil {
		t.Fatal(err)
	}
	secondCanonical, err := second.Canonicalize()
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(firstCanonical, secondCanonical) {
		t.Fatal("Canonicalize() did not order equivalent candidate values")
	}
}

func TestCanonicalizePreservesEvaluatorOwnedSelectionOrder(t *testing.T) {
	first := validCandidate()
	first.ResponsibilityBindings = []ResponsibilitySelection{
		{CandidateAgentDefinitionIDs: []string{"agent-developer"}},
		{CandidateAgentDefinitionIDs: []string{"agent-reviewer"}},
	}
	second := first
	second.ResponsibilityBindings = append([]ResponsibilitySelection(nil), first.ResponsibilityBindings...)
	second.ResponsibilityBindings[0], second.ResponsibilityBindings[1] = second.ResponsibilityBindings[1], second.ResponsibilityBindings[0]

	firstCanonical, err := first.Canonicalize()
	if err != nil {
		t.Fatal(err)
	}
	secondCanonical, err := second.Canonicalize()
	if err != nil {
		t.Fatal(err)
	}
	firstDigest, secondDigest := digest(firstCanonical), digest(secondCanonical)
	if firstDigest == secondDigest {
		t.Fatal("swapping selections for different Solution slots did not change candidate identity")
	}
	evaluation := validEvaluation(first)
	evaluation.CandidateDigest = firstDigest
	if !evaluation.IsStaleFor(secondDigest) {
		t.Fatal("evaluation remained current after selection slots were swapped")
	}
}

func TestResolvedOutputValidationFailsClosed(t *testing.T) {
	resolvedRepository := RepositoryBinding{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
		ProviderType: "github", DefaultBranch: "main", Display: RepositoryDisplay{Name: "payments", OwnerPath: "example/payments"},
	}
	resolvedTarget := ExternalTargetBinding{
		ExternalTargetCandidate:   ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test"},
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}

	tests := []struct {
		name     string
		validate func() error
	}{
		{"incomplete package", func() error { return validateResolvedPackages([]ResolvedPackage{{PackageType: "solution"}}) }},
		{"duplicate package", func() error {
			return validateResolvedPackages([]ResolvedPackage{{PackageType: "solution", PackageID: "software-delivery", Version: "1", ArtifactDigest: "sha256:a", InstallStatus: "active", VerificationStatus: "passed"}, {PackageType: "solution", PackageID: "software-delivery", Version: "2", ArtifactDigest: "sha256:b", InstallStatus: "active", VerificationStatus: "passed"}})
		}},
		{"repository without probe facts", func() error {
			value := resolvedRepository
			value.ProviderType = ""
			return validateResolvedRepositories([]RepositoryBinding{value})
		}},
		{"environment without usages", func() error { return validateEnvironmentBindings([]EnvironmentBinding{{EnvironmentID: "preview"}}) }},
		{"duplicate environment", func() error {
			return validateEnvironmentBindings([]EnvironmentBinding{{EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsagePreview}}, {EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsageObservation}}})
		}},
		{"target without driver", func() error {
			value := resolvedTarget
			value.DeploymentDriver = ExactDeploymentDriverRef{}
			return validateExternalTargetBindings([]ExternalTargetBinding{value})
		}},
		{"target without digest", func() error {
			value := resolvedTarget
			value.TargetConfigurationDigest = ""
			return validateExternalTargetBindings([]ExternalTargetBinding{value})
		}},
		{"target without concurrency key", func() error {
			value := resolvedTarget
			value.ConcurrencyKey = ""
			return validateExternalTargetBindings([]ExternalTargetBinding{value})
		}},
		{"duplicate target", func() error {
			return validateExternalTargetBindings([]ExternalTargetBinding{resolvedTarget, resolvedTarget})
		}},
		{"passed group with warning", func() error {
			return validateFindingConsistency(validEvaluation(validCandidate()).Checks, nil, []ProjectSetupFinding{{Group: ProjectSetupCheckAgentSlots}})
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.validate(); err == nil {
				t.Fatal("validation succeeded")
			}
		})
	}

	if err := validateResolvedRepositories([]RepositoryBinding{resolvedRepository}); err != nil {
		t.Fatalf("validateResolvedRepositories() error = %v", err)
	}
	if err := validateEnvironmentBindings([]EnvironmentBinding{{EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsagePreview}}}); err != nil {
		t.Fatalf("validateEnvironmentBindings() error = %v", err)
	}
	if err := validateExternalTargetBindings([]ExternalTargetBinding{resolvedTarget}); err != nil {
		t.Fatalf("validateExternalTargetBindings() error = %v", err)
	}
}

func TestCanonicalizationRejectsInvalidSchemaValue(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"broken": []byte(`{`)}
	if _, err := candidate.Canonicalize(); err == nil {
		t.Fatal("Canonicalize() accepted invalid JSON")
	}
}

func TestConfigurationDigestCanonicalizesResolvedSetup(t *testing.T) {
	setup := ProjectSolutionSetup{
		Solution:          validBeginProjectSetupRequest().Solution,
		ProjectParameters: ProjectParameters{"settings": []byte(`{"retries":3}`)},
		Repositories: []RepositoryBinding{
			{BindingKey: "secondary", RepositoryURI: "https://github.com/example/docs.git", Access: RepositoryAccessSecondaryRead, ProviderType: "github", DefaultBranch: "main", Display: RepositoryDisplay{Name: "docs", OwnerPath: "example/docs"}},
			{BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite, ProviderType: "github", DefaultBranch: "main", Display: RepositoryDisplay{Name: "payments", OwnerPath: "example/payments"}},
		},
		EnvironmentBindings: []EnvironmentBinding{{EnvironmentID: "staging", Usages: []EnvironmentUsage{EnvironmentUsageObservation, EnvironmentUsagePreview}}, {EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsagePreview}}},
		ExternalTargets: []ExternalTargetBinding{
			{ExternalTargetCandidate: ExternalTargetCandidate{BindingKey: "production", Display: ExternalTargetDisplay{Name: "Production"}, TargetType: "kubernetes", Environment: "production", EndpointURI: "https://prod.example.test"}, DeploymentDriver: ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest}, TargetConfigurationDigest: validTestDigest, ConcurrencyKey: "production"},
			{ExternalTargetCandidate: ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://staging.example.test"}, DeploymentDriver: ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest}, TargetConfigurationDigest: validTestDigest, ConcurrencyKey: "staging"},
		},
		TeamBindings: []ResponsibilityBinding{{ResponsibilityID: "reviewer", CandidateAgentDefinitionIDs: []string{"agent-b", "agent-a"}, RequiredCapabilities: []string{"review", "code"}}},
	}
	reordered := setup
	reordered.Repositories = []RepositoryBinding{setup.Repositories[1], setup.Repositories[0]}
	reordered.EnvironmentBindings = []EnvironmentBinding{setup.EnvironmentBindings[1], setup.EnvironmentBindings[0]}
	reordered.ExternalTargets = []ExternalTargetBinding{setup.ExternalTargets[1], setup.ExternalTargets[0]}
	reordered.TeamBindings = []ResponsibilityBinding{{ResponsibilityID: "reviewer", CandidateAgentDefinitionIDs: []string{"agent-a", "agent-b"}, RequiredCapabilities: []string{"code", "review"}}}

	if setup.ConfigurationDigest() != reordered.ConfigurationDigest() {
		t.Fatalf("ConfigurationDigest() = %q and %q for equivalent setups", setup.ConfigurationDigest(), reordered.ConfigurationDigest())
	}
}

func TestFindingAndCandidateCorrespondenceConsistency(t *testing.T) {
	checks := validEvaluation(validCandidate()).Checks
	checks[2].Status = ProjectSetupCheckWarning
	warning := ProjectSetupFinding{Group: ProjectSetupCheckAgentSlots}
	if err := validateFindingConsistency(checks, nil, []ProjectSetupFinding{warning}); err != nil {
		t.Fatalf("warning consistency error = %v", err)
	}
	checks[2].Status = ProjectSetupCheckBlocked
	if err := validateFindingConsistency(checks, []ProjectSetupFinding{{Group: ProjectSetupCheckAgentSlots}}, nil); err != nil {
		t.Fatalf("blocker consistency error = %v", err)
	}

	candidate := ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://staging.example.test"}
	binding := ExternalTargetBinding{ExternalTargetCandidate: candidate, DeploymentDriver: ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest}, TargetConfigurationDigest: validTestDigest, ConcurrencyKey: "staging"}
	if err := matchingExternalTargetBindings([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{binding}); err != nil {
		t.Fatalf("matchingExternalTargetBindings() error = %v", err)
	}
	binding.EndpointURI = "https://forged.example.test"
	if err := matchingExternalTargetBindings([]ExternalTargetCandidate{candidate}, []ExternalTargetBinding{binding}); err == nil {
		t.Fatal("matchingExternalTargetBindings() accepted a substituted endpoint")
	}
}

func TestAdoptionUsesResolvedRepositoryAndSetupDigest(t *testing.T) {
	candidate := validCandidate()
	candidate.Repositories = []RepositoryCandidate{{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
	}}
	evaluation := validEvaluation(candidate)
	evaluation.Repositories = []RepositoryBinding{{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
		ProviderType: "github", DefaultBranch: "main", Display: RepositoryDisplay{Name: "payments", OwnerPath: "example/payments"},
	}}
	evaluation.EnvironmentBindings = []EnvironmentBinding{{EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsagePreview}}}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "resolved-repository"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	if adopted.SolutionSetup.Repositories[0].ProviderType != "github" {
		t.Fatalf("resolved repository = %#v", adopted.SolutionSetup.Repositories[0])
	}
	if adopted.ConfigurationDigest != adopted.SolutionSetup.ConfigurationDigest() {
		t.Fatalf("ConfigurationDigest = %q, want adopted setup digest %q", adopted.ConfigurationDigest, adopted.SolutionSetup.ConfigurationDigest())
	}
}

func TestAdoptionRejectsMalformedOrUnmatchedResolvedOutput(t *testing.T) {
	candidate := validCandidate()
	candidate.Repositories = []RepositoryCandidate{{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
	}}
	evaluation := validEvaluation(candidate)
	evaluation.Repositories = []RepositoryBinding{{
		BindingKey: "primary", RepositoryURI: "https://github.com/example/payments.git", Access: RepositoryAccessPrimaryWrite,
		ProviderType: "github", DefaultBranch: "main", Display: RepositoryDisplay{Name: "payments", OwnerPath: "example/payments"},
	}}
	evaluation.ExternalTargets = []ExternalTargetBinding{{
		ExternalTargetCandidate: ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test"},
	}}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "reject-resolved-output"}

	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); err == nil {
		t.Fatal("AdoptProjectSolutionSetup() accepted malformed resolved target")
	}

	evaluation.ExternalTargets = nil
	evaluation.Repositories[0].BindingKey = "forged"
	if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation}); err == nil {
		t.Fatal("AdoptProjectSolutionSetup() accepted a resolved repository that does not correspond to the candidate")
	}
}

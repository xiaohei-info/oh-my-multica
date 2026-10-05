package project

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestProjectContractValidationRejectsIncompleteValues(t *testing.T) {
	validSolution := validBeginProjectSetupRequest().Solution
	validDriver := ExactDeploymentDriverRef{DriverID: "kubernetes", Version: "1.0.0", ArtifactDigest: validTestDigest}
	validRepository := validCandidate().Repositories[0]
	validTarget := ExternalTargetCandidate{
		BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test",
	}
	validBinding := validEvaluation(validCandidate()).TeamBindings[0]

	tests := []struct {
		name     string
		validate func() error
	}{
		{"solution missing id", func() error { value := validSolution; value.SolutionID = ""; return value.Validate() }},
		{"solution missing version", func() error { value := validSolution; value.Version = ""; return value.Validate() }},
		{"solution missing digest", func() error { value := validSolution; value.ArtifactDigest = ""; return value.Validate() }},
		{"driver missing id", func() error { value := validDriver; value.DriverID = ""; return value.Validate() }},
		{"driver missing version", func() error { value := validDriver; value.Version = ""; return value.Validate() }},
		{"driver missing digest", func() error { value := validDriver; value.ArtifactDigest = ""; return value.Validate() }},
		{"display missing name", func() error { return (ProjectDisplay{Description: "description"}).Validate() }},
		{"display missing description", func() error { return (ProjectDisplay{Name: "name"}).Validate() }},
		{"begin missing scope", func() error {
			request := validBeginProjectSetupRequest()
			request.OwnerScopeID = ""
			return request.Validate()
		}},
		{"repository missing key", func() error { value := validRepository; value.BindingKey = ""; return value.Validate() }},
		{"repository missing uri", func() error { value := validRepository; value.RepositoryURI = ""; return value.Validate() }},
		{"repository missing access", func() error { value := validRepository; value.Access = ""; return value.Validate() }},
		{"target missing key", func() error { value := validTarget; value.BindingKey = ""; return value.Validate() }},
		{"target missing display", func() error { value := validTarget; value.Display.Name = ""; return value.Validate() }},
		{"target missing type", func() error { value := validTarget; value.TargetType = ""; return value.Validate() }},
		{"target missing environment", func() error { value := validTarget; value.Environment = ""; return value.Validate() }},
		{"target missing endpoint", func() error { value := validTarget; value.EndpointURI = ""; return value.Validate() }},
		{"binding negative selection index", func() error { value := validBinding; value.SelectionIndex = -1; return value.Validate() }},
		{"binding missing responsibility", func() error { value := validBinding; value.ResponsibilityID = ""; return value.Validate() }},
		{"binding missing setup requirement", func() error { value := validBinding; value.SetupRequirement = ""; return value.Validate() }},
		{"binding duplicate candidate", func() error {
			value := validBinding
			value.CandidateAgentDefinitionIDs = []string{"agent-1", "agent-1"}
			return value.Validate()
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
}

func TestExactComponentReferencesRejectAliasesRangesAndMalformedDigests(t *testing.T) {
	solution := validBeginProjectSetupRequest().Solution
	driver := ExactDeploymentDriverRef{DriverID: "kubernetes", Version: "1.0.0", ArtifactDigest: validTestDigest}
	for _, version := range []string{"0.0.0", "1.2.3-rc.1", "1.2.3-rc.1+build.7"} {
		value := solution
		value.Version = version
		if err := value.Validate(); err != nil {
			t.Fatalf("Validate() rejected strict SemVer %q: %v", version, err)
		}
	}
	for _, version := range []string{"latest", "1", "v1.0.0", "^1.0.0", ">=1.0.0", "01.0.0", "1.0.0-", "1.0.0-rc..1", "1.0.0-rc.@", "1.0.0-01"} {
		for _, validate := range []func() error{
			func() error { value := solution; value.Version = version; return value.Validate() },
			func() error { value := driver; value.Version = version; return value.Validate() },
		} {
			if err := validate(); err == nil {
				t.Fatalf("Validate() accepted non-exact version %q", version)
			}
		}
	}
	for _, digest := range []string{"sha256:short", "sha512:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "sha256:0123456789ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef"} {
		value := solution
		value.ArtifactDigest = digest
		if err := value.Validate(); err == nil {
			t.Fatalf("Validate() accepted malformed digest %q", digest)
		}
	}
}

func TestReadinessAndAdoptionRequireExactDigestFacts(t *testing.T) {
	validPackage := ResolvedPackage{PackageType: "solution", PackageID: "software-delivery", Version: "1.0.0", ArtifactDigest: validTestDigest, InstallStatus: PackageInstallStatusActive, VerificationStatus: PackageVerificationStatusPassed}
	if err := validateResolvedPackages([]ResolvedPackage{validPackage}); err != nil {
		t.Fatalf("validateResolvedPackages() error = %v", err)
	}
	for _, version := range []string{"latest", "1", "^1.0.0"} {
		value := validPackage
		value.Version = version
		if err := validateResolvedPackages([]ResolvedPackage{value}); err == nil {
			t.Fatalf("validateResolvedPackages() accepted version %q", version)
		}
	}
	for _, digest := range []string{"sha256:short", "sha512:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"} {
		value := validPackage
		value.ArtifactDigest = digest
		if err := validateResolvedPackages([]ResolvedPackage{value}); err == nil {
			t.Fatalf("validateResolvedPackages() accepted digest %q", digest)
		}

		evaluation := validEvaluation(validCandidate())
		evaluation.CandidateDigest = digest
		if err := evaluation.Validate(); err == nil {
			t.Fatalf("evaluation.Validate() accepted candidate digest %q", digest)
		}

		request := AdoptProjectSolutionSetupRequest{ProjectID: "project-1", Candidate: validCandidate(), CandidateDigest: digest, IdempotencyKey: "digest-validation"}
		if err := request.Validate(); err == nil {
			t.Fatalf("adoption Validate() accepted candidate digest %q", digest)
		}

		target := ExternalTargetBinding{
			ExternalTargetCandidate:   ExternalTargetCandidate{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test"},
			DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes", Version: "1.0.0", ArtifactDigest: validTestDigest},
			TargetConfigurationDigest: digest,
			ConcurrencyKey:            "staging",
		}
		if err := validateExternalTargetBindings([]ExternalTargetBinding{target}); err == nil {
			t.Fatalf("validateExternalTargetBindings() accepted target digest %q", digest)
		}
	}
}

func TestResolvedSolutionSummaryMustMatchEvaluatedSolution(t *testing.T) {
	candidate := validCandidate()
	validSummary := ResolvedPackage{
		PackageType:        "solution",
		PackageID:          candidate.Solution.SolutionID,
		Version:            candidate.Solution.Version,
		ArtifactDigest:     candidate.Solution.ArtifactDigest,
		InstallStatus:      PackageInstallStatusActive,
		VerificationStatus: PackageVerificationStatusPassed,
	}
	evaluation := validEvaluation(candidate)
	evaluation.ResolvedPackages = []ResolvedPackage{validSummary}
	if err := evaluation.Validate(); err != nil {
		t.Fatalf("evaluation.Validate() error = %v", err)
	}

	for _, mutate := range []struct {
		name  string
		apply func(*ResolvedPackage)
	}{
		{"package id", func(value *ResolvedPackage) { value.PackageID = "other-solution" }},
		{"version", func(value *ResolvedPackage) { value.Version = "1.0.1" }},
		{"digest", func(value *ResolvedPackage) { value.ArtifactDigest = otherTestDigest }},
	} {
		t.Run(mutate.name, func(t *testing.T) {
			invalid := validEvaluation(candidate)
			invalid.ResolvedPackages = []ResolvedPackage{validSummary}
			mutate.apply(&invalid.ResolvedPackages[0])
			if err := invalid.Validate(); !errors.Is(err, ErrEvaluationMismatch) {
				t.Fatalf("evaluation.Validate() error = %v, want %v", err, ErrEvaluationMismatch)
			}

			request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: invalid.CandidateDigest, IdempotencyKey: "solution-summary-" + mutate.name}
			if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: invalid}); !errors.Is(err, ErrEvaluationMismatch) {
				t.Fatalf("AdoptProjectSolutionSetup() error = %v, want %v", err, ErrEvaluationMismatch)
			}
		})
	}

	evaluation.ResolvedPackages = append(evaluation.ResolvedPackages, validSummary)
	if err := evaluation.Validate(); err == nil {
		t.Fatal("evaluation.Validate() accepted duplicate Solution summaries")
	}
}

func TestResolvedPackageTypeIsClosedAndCannotBypassSolutionCorrelation(t *testing.T) {
	candidate := validCandidate()
	validSummary := ResolvedPackage{
		PackageType:        ResolvedPackageTypeSolution,
		PackageID:          candidate.Solution.SolutionID,
		Version:            candidate.Solution.Version,
		ArtifactDigest:     candidate.Solution.ArtifactDigest,
		InstallStatus:      PackageInstallStatusActive,
		VerificationStatus: PackageVerificationStatusPassed,
	}
	for _, packageType := range []ResolvedPackageType{ResolvedPackageTypeContent, ResolvedPackageTypeExtension} {
		value := validSummary
		value.PackageType = packageType
		if err := validateResolvedPackages([]ResolvedPackage{value}); err != nil {
			t.Fatalf("validateResolvedPackages() rejected %q: %v", packageType, err)
		}
	}
	for _, packageType := range []ResolvedPackageType{"", "Solution", "solution ", "solution\x00", "unknown"} {
		t.Run(string(packageType), func(t *testing.T) {
			invalid := validEvaluation(candidate)
			invalid.ResolvedPackages = []ResolvedPackage{validSummary}
			invalid.ResolvedPackages[0].PackageType = packageType
			invalid.ResolvedPackages[0].PackageID = "contradictory-solution"
			if err := invalid.Validate(); err == nil {
				t.Fatal("evaluation.Validate() accepted an invalid package type alias")
			}

			request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: invalid.CandidateDigest, IdempotencyKey: "package-type"}
			if _, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: invalid}); err == nil {
				t.Fatal("AdoptProjectSolutionSetup() accepted an invalid package type alias")
			}
		})
	}
}

func TestProjectSetupCandidateRejectsDuplicateBindings(t *testing.T) {
	tests := []struct {
		name      string
		candidate ProjectSetupCandidate
	}{
		{
			name:      "missing project",
			candidate: ProjectSetupCandidate{Solution: validBeginProjectSetupRequest().Solution},
		},
		{
			name: "duplicate repository",
			candidate: func() ProjectSetupCandidate {
				candidate := validCandidate()
				candidate.Repositories = append(candidate.Repositories, candidate.Repositories[0])
				return candidate
			}(),
		},
		{
			name: "duplicate target",
			candidate: func() ProjectSetupCandidate {
				candidate := validCandidate()
				candidate.ExternalTargets = []ExternalTargetCandidate{
					{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://one.example.test"},
					{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging two"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://two.example.test"},
				}
				return candidate
			}(),
		},
		{
			name: "duplicate responsibility candidate agent",
			candidate: func() ProjectSetupCandidate {
				candidate := validCandidate()
				candidate.ResponsibilityBindings[0].CandidateAgentDefinitionIDs = []string{"agent-definition-developer", "agent-definition-developer"}
				return candidate
			}(),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.candidate.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
}

func TestProjectSetupEvaluationRejectsInvalidResults(t *testing.T) {
	candidate := validCandidate()
	validFinding := ProjectSetupFinding{
		Code:        "credential.unavailable",
		Group:       ProjectSetupCheckAuthorizationGovernance,
		Subject:     FindingSubject{Kind: "credential_binding", CandidatePath: "repositories[0].credential_binding_id"},
		Message:     "凭证不可用",
		Remediation: "选择可用凭证",
		FixAction:   &FixAction{Type: FixActionFocusField, Target: "repositories[0].credential_binding_id"},
	}
	publicEvaluation := func() ProjectSetupEvaluation { return validEvaluation(candidate).ProjectSetupEvaluation }
	validBlocked := func() ProjectSetupEvaluation {
		evaluation := publicEvaluation()
		evaluation.Readiness = ProjectSetupReadinessBlocked
		evaluation.Checks[4].Status = ProjectSetupCheckBlocked
		evaluation.Blockers = []ProjectSetupFinding{validFinding}
		return evaluation
	}

	tests := []struct {
		name       string
		evaluation ProjectSetupEvaluation
	}{
		{"missing project", func() ProjectSetupEvaluation { value := publicEvaluation(); value.ProjectID = ""; return value }()},
		{"missing candidate digest", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.CandidateDigest = ""
			return value
		}()},
		{"missing evaluation time", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.EvaluatedAt = time.Time{}
			return value
		}()},
		{"invalid readiness", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Readiness = "unknown"
			return value
		}()},
		{"ready with blocker", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Blockers = []ProjectSetupFinding{validFinding}
			return value
		}()},
		{"blocked without blocker", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Readiness = ProjectSetupReadinessBlocked
			return value
		}()},
		{"missing group", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Checks = value.Checks[:4]
			return value
		}()},
		{"invalid group", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Checks[0].Group = "unknown"
			return value
		}()},
		{"duplicate group", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Checks[1].Group = value.Checks[0].Group
			return value
		}()},
		{"invalid status", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Checks[0].Status = "unknown"
			return value
		}()},
		{"missing summary", func() ProjectSetupEvaluation {
			value := publicEvaluation()
			value.Checks[0].Summary = ""
			return value
		}()},
		{"finding missing code", func() ProjectSetupEvaluation { value := validBlocked(); value.Blockers[0].Code = ""; return value }()},
		{"finding invalid group", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].Group = "unknown"
			return value
		}()},
		{"finding missing subject kind", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].Subject.Kind = ""
			return value
		}()},
		{"finding missing subject location", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].Subject = FindingSubject{Kind: "credential_binding"}
			return value
		}()},
		{"finding missing message", func() ProjectSetupEvaluation { value := validBlocked(); value.Blockers[0].Message = ""; return value }()},
		{"finding missing remediation", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].Remediation = ""
			return value
		}()},
		{"finding invalid fix action", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].FixAction = &FixAction{Type: "run_script", Target: "x"}
			return value
		}()},
		{"finding missing fix action target", func() ProjectSetupEvaluation {
			value := validBlocked()
			value.Blockers[0].FixAction = &FixAction{Type: FixActionOpenRoute}
			return value
		}()},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.evaluation.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}

	if err := validBlocked().Validate(); err != nil {
		t.Fatalf("blocked evaluation Validate() error = %v", err)
	}
}

func TestAdoptionRejectsMismatchesAndDoesNotMutateCurrentProject(t *testing.T) {
	candidate := validCandidate()
	current := Project{ProjectID: "project-1", Revision: 2}
	request := AdoptProjectSolutionSetupRequest{
		ProjectID: "project-1", ExpectedRevision: 2, Candidate: candidate, CandidateDigest: validEvaluation(candidate).CandidateDigest, IdempotencyKey: "key-1",
	}

	invalidRequests := []struct {
		name    string
		request AdoptProjectSolutionSetupRequest
	}{
		{"missing project", func() AdoptProjectSolutionSetupRequest { value := request; value.ProjectID = ""; return value }()},
		{"candidate project mismatch", func() AdoptProjectSolutionSetupRequest {
			value := request
			value.Candidate.ProjectID = "project-2"
			return value
		}()},
		{"missing idempotency key", func() AdoptProjectSolutionSetupRequest { value := request; value.IdempotencyKey = ""; return value }()},
	}
	for _, test := range invalidRequests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.request.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}

	cases := []struct {
		name      string
		current   Project
		validator ProjectSetupValidator
		want      error
	}{
		{"project mismatch", Project{ProjectID: "project-2", Revision: 2}, staticValidator{evaluation: validEvaluation(candidate)}, nil},
		{"missing validator", current, nil, nil},
		{"validator error", current, errorValidator{err: errors.New("probe unavailable")}, nil},
		{"invalid evaluation", current, staticValidator{evaluation: ProjectSetupValidationResult{}}, nil},
		{"evaluation mismatch", current, staticValidator{evaluation: func() ProjectSetupValidationResult {
			value := validEvaluation(candidate)
			value.ProjectID = "project-2"
			return value
		}()}, ErrEvaluationMismatch},
		{"blocked evaluation", current, staticValidator{evaluation: func() ProjectSetupValidationResult {
			value := validEvaluation(candidate)
			value.Readiness = ProjectSetupReadinessBlocked
			value.Checks[2].Status = ProjectSetupCheckBlocked
			value.Blockers = []ProjectSetupFinding{{Code: "agent_slot.unsatisfied", Group: ProjectSetupCheckAgentSlots, Subject: FindingSubject{Kind: "agent_slot", CandidatePath: "responsibility_bindings[0]"}, Message: "能力不足", Remediation: "选择兼容 Agent"}}
			return value
		}()}, ErrCandidateNotReady},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			_, err := AdoptProjectSolutionSetup(context.Background(), test.current, request, test.validator)
			if err == nil {
				t.Fatal("AdoptProjectSolutionSetup() succeeded")
			}
			if test.want != nil && !errors.Is(err, test.want) {
				t.Fatalf("error = %v, want %v", err, test.want)
			}
			if test.current.SolutionSetup != nil || test.current.Revision != 2 {
				t.Fatalf("current project was mutated: %#v", test.current)
			}
		})
	}
}

func TestAdoptionCopiesNormalizedSetupValues(t *testing.T) {
	candidate := validCandidate()
	candidate.ProjectParameters = ProjectParameters{"language": []byte(`"go"`)}
	candidate.PolicyOverrides = PolicyOverrides{"network": []byte(`"restricted"`)}
	candidate.ExternalTargets = []ExternalTargetCandidate{{BindingKey: "staging", Display: ExternalTargetDisplay{Name: "Staging"}, TargetType: "kubernetes", Environment: "staging", EndpointURI: "https://cluster.example.test"}}
	evaluation := validEvaluation(candidate)
	evaluation.EnvironmentBindings = []EnvironmentBinding{{EnvironmentID: "preview", Usages: []EnvironmentUsage{EnvironmentUsagePreview}}}
	evaluation.ExternalTargets = []ExternalTargetBinding{{
		ExternalTargetCandidate:   candidate.ExternalTargets[0],
		DeploymentDriver:          ExactDeploymentDriverRef{DriverID: "kubernetes-driver", Version: "1.0.0", ArtifactDigest: validTestDigest},
		TargetConfigurationDigest: validTestDigest,
		ConcurrencyKey:            "staging",
	}}
	request := AdoptProjectSolutionSetupRequest{ProjectID: candidate.ProjectID, ExpectedRevision: 1, Candidate: candidate, CandidateDigest: evaluation.CandidateDigest, IdempotencyKey: "copy-values"}

	adopted, err := AdoptProjectSolutionSetup(context.Background(), Project{ProjectID: candidate.ProjectID, Revision: 1}, request, staticValidator{evaluation: evaluation})
	if err != nil {
		t.Fatalf("AdoptProjectSolutionSetup() error = %v", err)
	}
	candidate.ProjectParameters["language"][1] = 'X'
	candidate.PolicyOverrides["network"][1] = 'X'
	evaluation.EnvironmentBindings[0].Usages[0] = "changed"
	if got := string(adopted.SolutionSetup.ProjectParameters["language"]); got != `"go"` {
		t.Fatalf("ProjectParameters alias input: %q", got)
	}
	if got := string(adopted.SolutionSetup.PolicyOverrides["network"]); got != `"restricted"` {
		t.Fatalf("PolicyOverrides alias input: %q", got)
	}
	if got := adopted.SolutionSetup.EnvironmentBindings[0].Usages[0]; got != "preview" {
		t.Fatalf("EnvironmentBindings alias input: %q", got)
	}
	if got := adopted.SolutionSetup.ExternalTargets[0].DeploymentDriver.DriverID; got != "kubernetes-driver" {
		t.Fatalf("ExternalTargets = %#v", adopted.SolutionSetup.ExternalTargets)
	}
}

func TestCreateWorkflowFromProjectSetupValidationRequiresCausalInputs(t *testing.T) {
	valid := CreateWorkflowFromProjectSetupRequest{
		ProjectID: "project-1", ExpectedProjectRevision: 3, ExpectedConfigurationDigest: validTestDigest,
		Goal: WorkflowGoal{Title: "Successor", Description: "Deliver successor"}, InputArtifactIDs: []string{"goal-1", "impact-1"}, IdempotencyKey: "create-project-1-v3",
		SuccessorContext: &SuccessorContext{PredecessorWorkflowName: "previous", ImpactAnalysisArtifactID: "impact-1", ApprovalDecisionID: "decision-1"},
	}
	if err := valid.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
	ordinary := valid
	ordinary.SuccessorContext = nil
	ordinary.InputArtifactIDs = nil
	if err := ordinary.Validate(); err != nil {
		t.Fatalf("ordinary workflow create Validate() error = %v", err)
	}
	clone := func() CreateWorkflowFromProjectSetupRequest {
		value := valid
		context := *valid.SuccessorContext
		value.SuccessorContext = &context
		value.InputArtifactIDs = append([]string(nil), valid.InputArtifactIDs...)
		return value
	}
	tests := []struct {
		name    string
		request CreateWorkflowFromProjectSetupRequest
	}{
		{"missing project", func() CreateWorkflowFromProjectSetupRequest { value := clone(); value.ProjectID = ""; return value }()},
		{"missing configuration", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.ExpectedConfigurationDigest = ""
			return value
		}()},
		{"malformed configuration digest", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.ExpectedConfigurationDigest = "sha256:short"
			return value
		}()},
		{"missing goal title", func() CreateWorkflowFromProjectSetupRequest { value := clone(); value.Goal.Title = ""; return value }()},
		{"missing goal description", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.Goal.Description = ""
			return value
		}()},
		{"duplicate input", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.InputArtifactIDs = []string{"goal-1", "goal-1"}
			return value
		}()},
		{"malformed workflow input", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.WorkflowInputs = ProjectParameters{"bad": []byte(`{`)}
			return value
		}()},
		{"missing idempotency", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.IdempotencyKey = ""
			return value
		}()},
		{"missing predecessor", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.SuccessorContext.PredecessorWorkflowName = ""
			return value
		}()},
		{"missing impact id", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.SuccessorContext.ImpactAnalysisArtifactID = ""
			return value
		}()},
		{"missing decision", func() CreateWorkflowFromProjectSetupRequest {
			value := clone()
			value.SuccessorContext.ApprovalDecisionID = ""
			return value
		}()},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.request.Validate(); err == nil {
				t.Fatal("Validate() succeeded")
			}
		})
	}
}

type errorValidator struct {
	err error
}

func (validator errorValidator) Evaluate(_ context.Context, _ ProjectSetupCandidate) (ProjectSetupValidationResult, error) {
	return ProjectSetupValidationResult{}, validator.err
}

package project

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var artifactDigestPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

var (
	ErrRevisionConflict   = errors.New("project revision conflict")
	ErrCandidateNotReady  = errors.New("project setup candidate is not ready")
	ErrEvaluationMismatch = errors.New("project setup evaluation does not match the adoption request")
	ErrProjectArchived    = errors.New("project is archived")
	ErrRevisionExhausted  = errors.New("project revision is exhausted")
)

func (reference ExactSolutionRef) Validate() error {
	if reference.SolutionID == "" {
		return required("solution.solution_id")
	}
	if reference.Version == "" {
		return required("solution.version")
	}
	if !validSemVer(reference.Version) {
		return fmt.Errorf("solution.version must be strict SemVer")
	}
	return validateSHA256Digest("solution.artifact_digest", reference.ArtifactDigest)
}

func (reference ExactDeploymentDriverRef) Validate() error {
	if reference.DriverID == "" {
		return required("deployment_driver.driver_id")
	}
	if reference.Version == "" {
		return required("deployment_driver.version")
	}
	if !validSemVer(reference.Version) {
		return fmt.Errorf("deployment_driver.version must be strict SemVer")
	}
	return validateSHA256Digest("deployment_driver.artifact_digest", reference.ArtifactDigest)
}

func validateSHA256Digest(field, value string) error {
	if value == "" {
		return required(field)
	}
	if !artifactDigestPattern.MatchString(value) {
		return fmt.Errorf("%s must be a sha256 digest", field)
	}
	return nil
}

func validSemVer(value string) bool {
	if strings.HasPrefix(value, "v") || strings.ContainsAny(value, " <>=~^*|") {
		return false
	}
	parts := strings.SplitN(value, "+", 2)
	coreAndPre := strings.SplitN(parts[0], "-", 2)
	core := strings.Split(coreAndPre[0], ".")
	if len(core) != 3 {
		return false
	}
	for _, part := range core {
		if !validSemVerNumericIdentifier(part) {
			return false
		}
	}
	if len(coreAndPre) == 2 && !validSemVerIdentifiers(coreAndPre[1], true) {
		return false
	}
	if len(parts) == 2 && !validSemVerIdentifiers(parts[1], false) {
		return false
	}
	return true
}

func validSemVerIdentifiers(value string, forbidNumericLeadingZero bool) bool {
	if value == "" {
		return false
	}
	for _, identifier := range strings.Split(value, ".") {
		if identifier == "" {
			return false
		}
		allDigits := true
		for _, character := range identifier {
			if !(character >= '0' && character <= '9') && !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') && character != '-' {
				return false
			}
			if character < '0' || character > '9' {
				allDigits = false
			}
		}
		if forbidNumericLeadingZero && allDigits && !validSemVerNumericIdentifier(identifier) {
			return false
		}
	}
	return true
}

func validSemVerNumericIdentifier(value string) bool {
	if value == "" || (len(value) > 1 && value[0] == '0') {
		return false
	}
	for _, character := range value {
		if character < '0' || character > '9' {
			return false
		}
	}
	return true
}

func (display ProjectDisplay) Validate() error {
	if display.Name == "" {
		return required("display.name")
	}
	if display.Description == "" {
		return required("display.description")
	}
	return nil
}

// Validate confirms that a BeginProjectSetup request has no implicit identity.
func (request BeginProjectSetupRequest) Validate() error {
	if err := request.Display.Validate(); err != nil {
		return err
	}
	if request.OwnerScopeID == "" {
		return required("owner_scope_id")
	}
	return request.Solution.Validate()
}

func (candidate RepositoryCandidate) Validate() error {
	if candidate.BindingKey == "" {
		return required("repositories[].binding_key")
	}
	if candidate.RepositoryURI == "" {
		return required("repositories[].repository_uri")
	}
	if !validRepositoryAccess(candidate.Access) {
		return fmt.Errorf("invalid repositories[].access %q", candidate.Access)
	}
	return nil
}

func (binding RepositoryBinding) Validate() error {
	if binding.BindingKey == "" {
		return required("repositories[].binding_key")
	}
	if binding.RepositoryURI == "" {
		return required("repositories[].repository_uri")
	}
	if !validRepositoryAccess(binding.Access) {
		return fmt.Errorf("invalid repositories[].access %q", binding.Access)
	}
	if binding.ProviderType == "" {
		return required("repositories[].provider_type")
	}
	if binding.DefaultBranch == "" {
		return required("repositories[].default_branch")
	}
	if binding.Display.Name == "" {
		return required("repositories[].display.name")
	}
	if binding.Display.OwnerPath == "" {
		return required("repositories[].display.owner_path")
	}
	return nil
}

func validRepositoryAccess(access RepositoryAccess) bool {
	switch access {
	case RepositoryAccessPrimaryWrite, RepositoryAccessSecondaryRead:
		return true
	default:
		return false
	}
}

func (candidate ExternalTargetCandidate) Validate() error {
	if candidate.BindingKey == "" {
		return required("external_targets[].binding_key")
	}
	if candidate.Display.Name == "" {
		return required("external_targets[].display.name")
	}
	if candidate.TargetType == "" {
		return required("external_targets[].target_type")
	}
	if candidate.Environment == "" {
		return required("external_targets[].environment")
	}
	if candidate.EndpointURI == "" {
		return required("external_targets[].endpoint_uri")
	}
	return nil
}

func (binding ResponsibilityBinding) Validate() error {
	if binding.ResponsibilityID == "" {
		return required("team_bindings[].responsibility_id")
	}
	if err := uniqueNonEmpty("team_bindings[].candidate_agent_definition_ids", binding.CandidateAgentDefinitionIDs); err != nil {
		return err
	}
	return uniqueNonEmpty("team_bindings[].required_capabilities", binding.RequiredCapabilities)
}

func (selection ResponsibilitySelection) Validate() error {
	return uniqueNonEmpty("responsibility_bindings[].candidate_agent_definition_ids", selection.CandidateAgentDefinitionIDs)
}

func (binding EvaluatedTeamBinding) Validate() error {
	if binding.SelectionIndex < 0 {
		return fmt.Errorf("team_bindings[].selection_index must not be negative")
	}
	if binding.SlotKey == "" {
		return required("team_bindings[].slot_key")
	}
	if !oneOf(string(binding.SetupRequirement), string(SetupRequirementRequired), string(SetupRequirementRecommended)) {
		return fmt.Errorf("invalid team_bindings[].setup_requirement %q", binding.SetupRequirement)
	}
	if binding.SlotKey != binding.ResponsibilityID {
		return fmt.Errorf("team_bindings[].responsibility_id must match slot_key")
	}
	return binding.ResponsibilityBinding().Validate()
}

func (binding EvaluatedTeamBinding) ResponsibilityBinding() ResponsibilityBinding {
	return ResponsibilityBinding{
		ResponsibilityID:            binding.ResponsibilityID,
		CandidateAgentDefinitionIDs: binding.CandidateAgentDefinitionIDs,
		RequiredCapabilities:        binding.RequiredCapabilities,
	}
}

// Validate confirms the transient candidate is complete enough for an
// authoritative evaluator to perform policy and dependency checks.
func (candidate ProjectSetupCandidate) Validate() error {
	if candidate.ProjectID == "" {
		return required("project_id")
	}
	if err := candidate.Solution.Validate(); err != nil {
		return err
	}
	if err := uniqueRepositoryCandidates(candidate.Repositories); err != nil {
		return err
	}
	if err := uniqueExternalTargetCandidates(candidate.ExternalTargets); err != nil {
		return err
	}
	return uniqueResponsibilityBindings(candidate.ResponsibilityBindings)
}

func uniqueRepositoryCandidates(candidates []RepositoryCandidate) error {
	keys := make(map[string]struct{}, len(candidates))
	for _, candidate := range candidates {
		if err := candidate.Validate(); err != nil {
			return err
		}
		if _, exists := keys[candidate.BindingKey]; exists {
			return duplicate("repositories[].binding_key", candidate.BindingKey)
		}
		keys[candidate.BindingKey] = struct{}{}
	}
	return nil
}

func uniqueExternalTargetCandidates(candidates []ExternalTargetCandidate) error {
	keys := make(map[string]struct{}, len(candidates))
	for _, candidate := range candidates {
		if err := candidate.Validate(); err != nil {
			return err
		}
		if _, exists := keys[candidate.BindingKey]; exists {
			return duplicate("external_targets[].binding_key", candidate.BindingKey)
		}
		keys[candidate.BindingKey] = struct{}{}
	}
	return nil
}

func uniqueResponsibilityBindings(bindings []ResponsibilitySelection) error {
	for _, binding := range bindings {
		if err := binding.Validate(); err != nil {
			return err
		}
	}
	return nil
}

// Validate confirms evaluation shape and fail-closed readiness semantics.
func (evaluation ProjectSetupEvaluation) Validate() error {
	if evaluation.ProjectID == "" {
		return required("project_id")
	}
	if err := evaluation.Solution.Validate(); err != nil {
		return err
	}
	if evaluation.CandidateDigest == "" {
		return required("candidate_digest")
	}
	if err := validateSHA256Digest("candidate_digest", evaluation.CandidateDigest); err != nil {
		return err
	}
	if evaluation.EvaluatedAt.IsZero() {
		return required("evaluated_at")
	}
	if err := validateChecks(evaluation.Checks); err != nil {
		return err
	}
	if err := validateFindings("blockers", evaluation.Blockers); err != nil {
		return err
	}
	if err := validateFindings("warnings", evaluation.Warnings); err != nil {
		return err
	}
	if err := validateResolvedPackages(evaluation.ResolvedPackages); err != nil {
		return err
	}
	if err := validateResolvedPackageCorrelation(evaluation.Solution, evaluation.ResolvedPackages); err != nil {
		return err
	}
	if err := validateFindingConsistency(evaluation.Checks, evaluation.Blockers, evaluation.Warnings); err != nil {
		return err
	}
	switch evaluation.Readiness {
	case ProjectSetupReadinessReady:
		if len(evaluation.Blockers) != 0 {
			return fmt.Errorf("ready evaluation contains blockers")
		}
		for _, check := range evaluation.Checks {
			if check.Status == ProjectSetupCheckBlocked {
				return fmt.Errorf("ready evaluation contains blocked check %q", check.Group)
			}
		}
		for _, resolved := range evaluation.ResolvedPackages {
			if resolved.InstallStatus != PackageInstallStatusActive || resolved.VerificationStatus != PackageVerificationStatusPassed {
				return fmt.Errorf("ready evaluation contains unusable resolved package %q", resolved.PackageID)
			}
		}
	case ProjectSetupReadinessBlocked:
		if len(evaluation.Blockers) == 0 {
			return fmt.Errorf("blocked evaluation has no blockers")
		}
	default:
		return fmt.Errorf("invalid readiness %q", evaluation.Readiness)
	}
	return nil
}

// ValidateForCandidate validates resolved output and confirms it belongs to the
// exact transient candidate whose digest was evaluated.
func (result ProjectSetupValidationResult) Validate() error {
	if err := result.ProjectSetupEvaluation.Validate(); err != nil {
		return err
	}
	if err := validateResolvedRepositories(result.Repositories); err != nil {
		return err
	}
	if err := validateEnvironmentBindings(result.EnvironmentBindings); err != nil {
		return err
	}
	if err := validateExternalTargetBindings(result.ExternalTargets); err != nil {
		return err
	}
	if err := validateTeamBindings(result.TeamBindings); err != nil {
		return err
	}
	return result.NormalizedValues.Validate()
}

// ValidateForCandidate validates evaluator-only resolved output and confirms it
// belongs to the exact transient candidate whose digest was evaluated.
func (result ProjectSetupValidationResult) ValidateForCandidate(candidate ProjectSetupCandidate) error {
	if err := result.Validate(); err != nil {
		return err
	}
	if result.ProjectID != candidate.ProjectID || result.Solution != candidate.Solution {
		return ErrEvaluationMismatch
	}
	if err := normalizedValuesCorrespondToCandidate(candidate, result.NormalizedValues); err != nil {
		return err
	}
	if err := matchingRepositoryBindingsForReadiness(candidate, result); err != nil {
		return err
	}
	if err := matchingExternalTargetBindingsForReadiness(candidate, result); err != nil {
		return err
	}
	if err := matchingTeamBindings(candidate.ResponsibilityBindings, result); err != nil {
		return err
	}
	if err := validateRequiredSlotBlockers(result); err != nil {
		return err
	}
	return validateFindingsForCandidate(candidate, result.Blockers, result.Warnings)
}

func validateTeamBindings(bindings []EvaluatedTeamBinding) error {
	slotKeys := make(map[string]struct{}, len(bindings))
	responsibilityIDs := make(map[string]struct{}, len(bindings))
	selectionIndexes := make(map[int]struct{}, len(bindings))
	for _, binding := range bindings {
		if err := binding.Validate(); err != nil {
			return err
		}
		if _, exists := slotKeys[binding.SlotKey]; exists {
			return duplicate("team_bindings[].slot_key", binding.SlotKey)
		}
		if _, exists := responsibilityIDs[binding.ResponsibilityID]; exists {
			return duplicate("team_bindings[].responsibility_id", binding.ResponsibilityID)
		}
		if _, exists := selectionIndexes[binding.SelectionIndex]; exists {
			return duplicate("team_bindings[].selection_index", strconv.Itoa(binding.SelectionIndex))
		}
		slotKeys[binding.SlotKey] = struct{}{}
		responsibilityIDs[binding.ResponsibilityID] = struct{}{}
		selectionIndexes[binding.SelectionIndex] = struct{}{}
	}
	return nil
}

func matchingTeamBindings(selections []ResponsibilitySelection, result ProjectSetupValidationResult) error {
	if len(selections) != len(result.TeamBindings) {
		return ErrEvaluationMismatch
	}
	for _, binding := range result.TeamBindings {
		if binding.SelectionIndex >= len(selections) || !sameStringSet(selections[binding.SelectionIndex].CandidateAgentDefinitionIDs, binding.CandidateAgentDefinitionIDs) {
			return ErrEvaluationMismatch
		}
	}
	return nil
}

func validateRequiredSlotBlockers(result ProjectSetupValidationResult) error {
	for _, binding := range result.TeamBindings {
		if binding.SetupRequirement != SetupRequirementRequired || len(binding.CandidateAgentDefinitionIDs) != 0 {
			continue
		}
		if result.Readiness != ProjectSetupReadinessBlocked || !checkHasStatus(result.Checks, ProjectSetupCheckAgentSlots, ProjectSetupCheckBlocked) {
			return fmt.Errorf("empty required team binding must block agent_slots")
		}
		path := fmt.Sprintf("responsibility_bindings[%d]", binding.SelectionIndex)
		matched := false
		for _, finding := range result.Blockers {
			if finding.Group == ProjectSetupCheckAgentSlots && strings.HasPrefix(finding.Code, "agent_slot.") && finding.Subject.Kind == "agent_slot" && finding.Subject.CandidatePath == path {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("empty required team binding lacks an agent_slot blocker for %s", path)
		}
	}
	return nil
}

func checkHasStatus(checks []ProjectSetupCheck, group ProjectSetupCheckGroup, status ProjectSetupCheckStatus) bool {
	for _, check := range checks {
		if check.Group == group && check.Status == status {
			return true
		}
	}
	return false
}

func sameStringSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	values := make(map[string]struct{}, len(left))
	for _, value := range left {
		values[value] = struct{}{}
	}
	for _, value := range right {
		if _, exists := values[value]; !exists {
			return false
		}
	}
	return true
}

// Validate verifies normalized values without allowing evaluation output to
// smuggle malformed schema or policy values into an adopted Project.
func (values ProjectSetupNormalizedValues) Validate() error {
	if _, err := values.Canonicalize(); err != nil {
		return err
	}
	return nil
}

// Canonicalize returns the only normalized values that may be persisted.
func (values ProjectSetupNormalizedValues) Canonicalize() (ProjectSetupNormalizedValues, error) {
	canonical := values
	var err error
	if canonical.ProjectParameters, err = canonicalizeRawMessages(values.ProjectParameters); err != nil {
		return ProjectSetupNormalizedValues{}, fmt.Errorf("normalized project_parameters: %w", err)
	}
	if canonical.PolicyOverrides, err = canonicalizeRawMessages(values.PolicyOverrides); err != nil {
		return ProjectSetupNormalizedValues{}, fmt.Errorf("normalized policy_overrides: %w", err)
	}
	return canonical, nil
}

func normalizedValuesCorrespondToCandidate(candidate ProjectSetupCandidate, values ProjectSetupNormalizedValues) error {
	if !sameProjectParameterKeys(candidate.ProjectParameters, values.ProjectParameters) {
		return ErrEvaluationMismatch
	}
	if !samePolicyOverrideKeys(candidate.PolicyOverrides, values.PolicyOverrides) {
		return ErrEvaluationMismatch
	}
	return nil
}

func sameProjectParameterKeys(candidate, normalized ProjectParameters) bool {
	if len(candidate) != len(normalized) {
		return false
	}
	for key := range candidate {
		if _, exists := normalized[key]; !exists {
			return false
		}
	}
	return true
}

func samePolicyOverrideKeys(candidate, normalized PolicyOverrides) bool {
	if len(candidate) != len(normalized) {
		return false
	}
	for key := range candidate {
		if _, exists := normalized[key]; !exists {
			return false
		}
	}
	return true
}

func matchingRepositoryBindingsForReadiness(candidate ProjectSetupCandidate, result ProjectSetupValidationResult) error {
	if result.Readiness == ProjectSetupReadinessReady {
		return matchingRepositoryBindings(candidate.Repositories, result.Repositories)
	}
	return matchingRepositoryBindingSubset(candidate.Repositories, result.Repositories, result.Checks, result.Blockers)
}

func matchingExternalTargetBindingsForReadiness(candidate ProjectSetupCandidate, result ProjectSetupValidationResult) error {
	if result.Readiness == ProjectSetupReadinessReady {
		return matchingExternalTargetBindings(candidate.ExternalTargets, result.ExternalTargets)
	}
	return matchingExternalTargetBindingSubset(candidate.ExternalTargets, result.ExternalTargets, result.Checks, result.Blockers)
}

func validateResolvedPackages(packages []ResolvedPackage) error {
	seen := make(map[string]struct{}, len(packages))
	for _, resolved := range packages {
		if !validResolvedPackageType(resolved.PackageType) || resolved.PackageID == "" || !validPackageInstallStatus(resolved.InstallStatus) || !validPackageVerificationStatus(resolved.VerificationStatus) {
			return fmt.Errorf("resolved_packages[] contains an incomplete package")
		}
		if !validSemVer(resolved.Version) {
			return fmt.Errorf("resolved_packages[].version must be strict SemVer")
		}
		if err := validateSHA256Digest("resolved_packages[].artifact_digest", resolved.ArtifactDigest); err != nil {
			return err
		}
		key := string(resolved.PackageType) + "/" + resolved.PackageID
		if _, exists := seen[key]; exists {
			return duplicate("resolved_packages", key)
		}
		seen[key] = struct{}{}
	}
	return nil
}

func validResolvedPackageType(packageType ResolvedPackageType) bool {
	switch packageType {
	case ResolvedPackageTypeSolution, ResolvedPackageTypeContent, ResolvedPackageTypeExtension:
		return true
	default:
		return false
	}
}

// validateResolvedPackageCorrelation prevents the optional readiness summary
// from describing a Solution release other than the exact one evaluated.
func validateResolvedPackageCorrelation(solution ExactSolutionRef, packages []ResolvedPackage) error {
	solutionSummaryCount := 0
	for _, resolved := range packages {
		if resolved.PackageType != ResolvedPackageTypeSolution {
			continue
		}
		solutionSummaryCount++
		if solutionSummaryCount > 1 {
			return duplicate("resolved_packages[].package_type", "solution")
		}
		if resolved.PackageID != solution.SolutionID || resolved.Version != solution.Version || resolved.ArtifactDigest != solution.ArtifactDigest {
			return ErrEvaluationMismatch
		}
	}
	return nil
}

func validPackageInstallStatus(status PackageInstallStatus) bool {
	return status == PackageInstallStatusActive || status == PackageInstallStatusInactive
}

func validPackageVerificationStatus(status PackageVerificationStatus) bool {
	return status == PackageVerificationStatusPassed || status == PackageVerificationStatusFailed
}

func validateResolvedRepositories(bindings []RepositoryBinding) error {
	seen := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		if err := binding.Validate(); err != nil {
			return err
		}
		if _, exists := seen[binding.BindingKey]; exists {
			return duplicate("repositories[].binding_key", binding.BindingKey)
		}
		seen[binding.BindingKey] = struct{}{}
	}
	return nil
}

func validateEnvironmentBindings(bindings []EnvironmentBinding) error {
	seen := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		if binding.EnvironmentID == "" || len(binding.Usages) == 0 {
			return fmt.Errorf("environment_bindings[] contains an incomplete binding")
		}
		usages := make(map[EnvironmentUsage]struct{}, len(binding.Usages))
		for _, usage := range binding.Usages {
			if usage != EnvironmentUsagePreview && usage != EnvironmentUsageObservation {
				return fmt.Errorf("invalid environment_bindings[].usage %q", usage)
			}
			if _, exists := usages[usage]; exists {
				return duplicate("environment_bindings[].usages", string(usage))
			}
			usages[usage] = struct{}{}
		}
		if _, exists := seen[binding.EnvironmentID]; exists {
			return duplicate("environment_bindings[].environment_id", binding.EnvironmentID)
		}
		seen[binding.EnvironmentID] = struct{}{}
	}
	return nil
}

func validateExternalTargetBindings(bindings []ExternalTargetBinding) error {
	seen := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		if err := binding.ExternalTargetCandidate.Validate(); err != nil {
			return err
		}
		if err := binding.DeploymentDriver.Validate(); err != nil {
			return err
		}
		if binding.ConcurrencyKey == "" {
			return fmt.Errorf("external_targets[] contains incomplete resolved values")
		}
		if err := validateSHA256Digest("external_targets[].target_configuration_digest", binding.TargetConfigurationDigest); err != nil {
			return err
		}
		if _, exists := seen[binding.BindingKey]; exists {
			return duplicate("external_targets[].binding_key", binding.BindingKey)
		}
		seen[binding.BindingKey] = struct{}{}
	}
	return nil
}

func validateFindingConsistency(checks []ProjectSetupCheck, blockers, warnings []ProjectSetupFinding) error {
	blockerCounts := findingCounts(blockers)
	warningCounts := findingCounts(warnings)
	for _, check := range checks {
		switch check.Status {
		case ProjectSetupCheckPassed, ProjectSetupCheckNotApplicable:
			if blockerCounts[check.Group] != 0 || warningCounts[check.Group] != 0 {
				return fmt.Errorf("check group %q has findings inconsistent with status %q", check.Group, check.Status)
			}
		case ProjectSetupCheckWarning:
			if blockerCounts[check.Group] != 0 || warningCounts[check.Group] == 0 {
				return fmt.Errorf("check group %q has findings inconsistent with warning status", check.Group)
			}
		case ProjectSetupCheckBlocked:
			if blockerCounts[check.Group] == 0 {
				return fmt.Errorf("blocked check group %q has no blocker", check.Group)
			}
		}
	}
	return nil
}

func findingCounts(findings []ProjectSetupFinding) map[ProjectSetupCheckGroup]int {
	counts := make(map[ProjectSetupCheckGroup]int)
	for _, finding := range findings {
		counts[finding.Group]++
	}
	return counts
}

func matchingRepositoryBindings(candidates []RepositoryCandidate, bindings []RepositoryBinding) error {
	if len(candidates) != len(bindings) {
		return fmt.Errorf("resolved repositories do not correspond to candidate repositories")
	}
	byKey := make(map[string]RepositoryCandidate, len(candidates))
	for _, candidate := range candidates {
		byKey[candidate.BindingKey] = candidate
	}
	for _, binding := range bindings {
		candidate, exists := byKey[binding.BindingKey]
		candidateURI, candidateErr := NormalizeRepositoryURI(candidate.RepositoryURI)
		bindingURI, bindingErr := NormalizeRepositoryURI(binding.RepositoryURI)
		if !exists || candidateErr != nil || bindingErr != nil || candidateURI != bindingURI || candidate.Access != binding.Access || candidate.CredentialBindingID != binding.CredentialBindingID {
			return fmt.Errorf("resolved repository %q does not correspond to candidate", binding.BindingKey)
		}
	}
	return nil
}

func matchingRepositoryBindingSubset(candidates []RepositoryCandidate, bindings []RepositoryBinding, checks []ProjectSetupCheck, blockers []ProjectSetupFinding) error {
	if len(bindings) > len(candidates) {
		return fmt.Errorf("resolved repositories do not correspond to candidate repositories")
	}
	byKey := make(map[string]RepositoryCandidate, len(candidates))
	for _, candidate := range candidates {
		byKey[candidate.BindingKey] = candidate
	}
	resolved := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		candidate, exists := byKey[binding.BindingKey]
		candidateURI, candidateErr := NormalizeRepositoryURI(candidate.RepositoryURI)
		bindingURI, bindingErr := NormalizeRepositoryURI(binding.RepositoryURI)
		if !exists || candidateErr != nil || bindingErr != nil || candidateURI != bindingURI || candidate.Access != binding.Access || candidate.CredentialBindingID != binding.CredentialBindingID {
			return fmt.Errorf("resolved repository %q does not correspond to candidate", binding.BindingKey)
		}
		resolved[binding.BindingKey] = struct{}{}
	}
	for index, candidate := range candidates {
		if _, exists := resolved[candidate.BindingKey]; !exists && !hasProbeBlockerForCandidatePath(checks, blockers, "repositories["+strconv.Itoa(index)+"]", "repository", "repository.") {
			return fmt.Errorf("missing resolved repository %q has no blocker", candidate.BindingKey)
		}
	}
	return nil
}

func matchingExternalTargetBindings(candidates []ExternalTargetCandidate, bindings []ExternalTargetBinding) error {
	if len(candidates) != len(bindings) {
		return fmt.Errorf("resolved external targets do not correspond to candidate external targets")
	}
	byKey := make(map[string]ExternalTargetCandidate, len(candidates))
	for _, candidate := range candidates {
		byKey[candidate.BindingKey] = candidate
	}
	for _, binding := range bindings {
		candidate, exists := byKey[binding.BindingKey]
		candidateURI, candidateErr := NormalizeTargetURI(candidate.EndpointURI)
		bindingURI, bindingErr := NormalizeTargetURI(binding.EndpointURI)
		if !exists || candidateErr != nil || bindingErr != nil || candidateURI != bindingURI || candidate.Display != binding.Display || candidate.TargetType != binding.TargetType || candidate.Environment != binding.Environment || candidate.CredentialBindingID != binding.CredentialBindingID {
			return fmt.Errorf("resolved external target %q does not correspond to candidate", binding.BindingKey)
		}
	}
	return nil
}

func matchingExternalTargetBindingSubset(candidates []ExternalTargetCandidate, bindings []ExternalTargetBinding, checks []ProjectSetupCheck, blockers []ProjectSetupFinding) error {
	if len(bindings) > len(candidates) {
		return fmt.Errorf("resolved external targets do not correspond to candidate external targets")
	}
	byKey := make(map[string]ExternalTargetCandidate, len(candidates))
	for _, candidate := range candidates {
		byKey[candidate.BindingKey] = candidate
	}
	resolved := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		candidate, exists := byKey[binding.BindingKey]
		candidateURI, candidateErr := NormalizeTargetURI(candidate.EndpointURI)
		bindingURI, bindingErr := NormalizeTargetURI(binding.EndpointURI)
		if !exists || candidateErr != nil || bindingErr != nil || candidateURI != bindingURI || candidate.Display != binding.Display || candidate.TargetType != binding.TargetType || candidate.Environment != binding.Environment || candidate.CredentialBindingID != binding.CredentialBindingID {
			return fmt.Errorf("resolved external target %q does not correspond to candidate", binding.BindingKey)
		}
		resolved[binding.BindingKey] = struct{}{}
	}
	for index, candidate := range candidates {
		if _, exists := resolved[candidate.BindingKey]; !exists && !hasProbeBlockerForCandidatePath(checks, blockers, "external_targets["+strconv.Itoa(index)+"]", "external_target", "external_target.") {
			return fmt.Errorf("missing resolved external target %q has no blocker", candidate.BindingKey)
		}
	}
	return nil
}

func hasProbeBlockerForCandidatePath(checks []ProjectSetupCheck, blockers []ProjectSetupFinding, path, kind, codePrefix string) bool {
	if !checkIsBlocked(checks, ProjectSetupCheckRepositoryExternal) {
		return false
	}
	for _, blocker := range blockers {
		if blocker.Group == ProjectSetupCheckRepositoryExternal && blocker.Subject.Kind == kind && strings.HasPrefix(blocker.Code, codePrefix) && (blocker.Subject.CandidatePath == path || strings.HasPrefix(blocker.Subject.CandidatePath, path+".")) {
			return true
		}
	}
	return false
}

func checkIsBlocked(checks []ProjectSetupCheck, group ProjectSetupCheckGroup) bool {
	for _, check := range checks {
		if check.Group == group {
			return check.Status == ProjectSetupCheckBlocked
		}
	}
	return false
}

// NormalizeProbeURI normalizes an HTTP(S) target endpoint. Repository identity
// has separate rules; callers comparing repository probes use NormalizeRepositoryURI.
func NormalizeProbeURI(raw string) (string, error) {
	return NormalizeTargetURI(raw)
}

// NormalizeTargetURI accepts only HTTP(S) endpoint equivalences that are safe
// for a generic external target. In particular, non-root trailing slashes are
// preserved because they can identify a different remote resource.
func NormalizeTargetURI(raw string) (string, error) {
	return normalizeHTTPURI(raw, false)
}

// NormalizeRepositoryURI applies repository-specific URI identity rules. It
// accepts HTTP(S), SSH authority URIs, and standard SCP-like Git syntax.
func NormalizeRepositoryURI(raw string) (string, error) {
	if !strings.Contains(raw, "://") {
		return normalizeSCPLikeGitURI(raw)
	}
	uri, err := url.Parse(raw)
	if err != nil || uri.Scheme == "" {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	switch strings.ToLower(uri.Scheme) {
	case "http", "https":
		if uri.User != nil {
			return "", fmt.Errorf("invalid repository URI %q", raw)
		}
		return normalizeHTTPURI(raw, true)
	case "ssh":
		return normalizeSSHRepositoryURI(uri, raw)
	default:
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
}

func normalizeHTTPURI(raw string, repositoryPath bool) (string, error) {
	uri, err := url.Parse(raw)
	if err != nil || uri.Scheme == "" || uri.Host == "" || uri.User != nil || uri.RawQuery != "" || uri.Fragment != "" {
		return "", fmt.Errorf("invalid probe URI %q", raw)
	}
	scheme := strings.ToLower(uri.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", fmt.Errorf("invalid probe URI %q", raw)
	}
	authority, err := normalizedAuthority(uri, scheme, "")
	if err != nil {
		return "", fmt.Errorf("invalid probe URI %q", raw)
	}
	path, err := normalizeEscapedPath(uri.EscapedPath())
	if err != nil {
		return "", fmt.Errorf("invalid probe URI %q", raw)
	}
	if path == "" {
		path = "/"
	}
	if repositoryPath {
		path = strings.TrimRight(path, "/")
		if path == "" {
			return "", fmt.Errorf("invalid repository URI %q", raw)
		}
	}
	return scheme + "://" + authority + path, nil
}

func normalizeSSHRepositoryURI(uri *url.URL, raw string) (string, error) {
	if uri.Host == "" || uri.User == nil || uri.User.Username() == "" || uri.RawQuery != "" || uri.Fragment != "" {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	if _, hasPassword := uri.User.Password(); hasPassword {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	authority, err := normalizedAuthority(uri, "ssh", uri.User.Username()+"@")
	if err != nil {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	path, err := normalizeEscapedPath(uri.EscapedPath())
	if err != nil {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	path = strings.TrimRight(path, "/")
	if path == "" || path == "/" {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	return "ssh://" + authority + path, nil
}

func normalizeSCPLikeGitURI(raw string) (string, error) {
	if strings.Count(raw, "@") != 1 {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	at := strings.IndexByte(raw, '@')
	if at <= 0 || at == len(raw)-1 {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	user := raw[:at]
	remainder := raw[at+1:]
	host, path, found := strings.Cut(remainder, ":")
	if strings.HasPrefix(remainder, "[") {
		end := strings.Index(remainder, "]:")
		if end <= 1 {
			return "", fmt.Errorf("invalid repository URI %q", raw)
		}
		host, path, found = remainder[:end+1], remainder[end+2:], true
	}
	if !found || host == "" || path == "" || strings.ContainsAny(user, ":/@ \t\r\n") || strings.ContainsAny(host, "/@ \t\r\n") {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	if !strings.HasPrefix(host, "[") && strings.Contains(host, ":") {
		return "", fmt.Errorf("invalid repository URI %q", raw)
	}
	if !strings.HasPrefix(path, "/") {
		path = "/~/" + strings.TrimPrefix(path, "~/")
	}
	uri := &url.URL{Host: host, User: url.User(user), Path: path}
	return normalizeSSHRepositoryURI(uri, raw)
}

func normalizedAuthority(uri *url.URL, scheme, userPrefix string) (string, error) {
	host := uri.Hostname()
	if host == "" {
		return "", errors.New("missing host")
	}
	if strings.Contains(host, "%") {
		return "", errors.New("scoped IPv6 is unsupported")
	}
	host = strings.ToLower(host)
	port := uri.Port()
	if port != "" && !((scheme == "http" && port == "80") || (scheme == "https" && port == "443") || (scheme == "ssh" && port == "22")) {
		host = net.JoinHostPort(host, port)
	} else if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	return userPrefix + host, nil
}

func normalizeEscapedPath(path string) (string, error) {
	var normalized strings.Builder
	for index := 0; index < len(path); index++ {
		if path[index] != '%' {
			normalized.WriteByte(path[index])
			continue
		}
		if index+2 >= len(path) {
			return "", errors.New("incomplete escape")
		}
		value, err := strconv.ParseUint(path[index+1:index+3], 16, 8)
		if err != nil {
			return "", errors.New("invalid escape")
		}
		character := byte(value)
		if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || strings.ContainsRune("-_~", rune(character)) {
			normalized.WriteByte(character)
		} else {
			normalized.WriteByte('%')
			normalized.WriteString(strings.ToUpper(path[index+1 : index+3]))
		}
		index += 2
	}
	return normalized.String(), nil
}

func validateChecks(checks []ProjectSetupCheck) error {
	expected := map[ProjectSetupCheckGroup]struct{}{
		ProjectSetupCheckSolutionPackage:             {},
		ProjectSetupCheckRepositoryExternal:          {},
		ProjectSetupCheckAgentSlots:                  {},
		ProjectSetupCheckRuntimeWorkspaceEnvironment: {},
		ProjectSetupCheckAuthorizationGovernance:     {},
	}
	if len(checks) != len(expected) {
		return fmt.Errorf("checks must contain exactly %d groups", len(expected))
	}
	seen := make(map[ProjectSetupCheckGroup]struct{}, len(checks))
	for _, check := range checks {
		if _, allowed := expected[check.Group]; !allowed {
			return fmt.Errorf("invalid check group %q", check.Group)
		}
		if _, exists := seen[check.Group]; exists {
			return duplicate("checks[].group", string(check.Group))
		}
		if !validCheckStatus(check.Status) {
			return fmt.Errorf("invalid check status %q", check.Status)
		}
		if check.Summary == "" {
			return required("checks[].summary")
		}
		seen[check.Group] = struct{}{}
	}
	return nil
}

func validCheckStatus(status ProjectSetupCheckStatus) bool {
	switch status {
	case ProjectSetupCheckPassed, ProjectSetupCheckWarning, ProjectSetupCheckBlocked, ProjectSetupCheckNotApplicable:
		return true
	default:
		return false
	}
}

func validateFindings(field string, findings []ProjectSetupFinding) error {
	for _, finding := range findings {
		if finding.Code == "" {
			return required(field + "[].code")
		}
		if !validCheckGroup(finding.Group) {
			return fmt.Errorf("invalid %s[].group %q", field, finding.Group)
		}
		if finding.Subject.Kind == "" {
			return required(field + "[].subject.kind")
		}
		if finding.Subject.CandidatePath == "" && finding.Subject.ResourceID == "" {
			return fmt.Errorf("%s[].subject must identify a candidate path or resource", field)
		}
		if finding.Message == "" {
			return required(field + "[].message")
		}
		if finding.Remediation == "" {
			return required(field + "[].remediation")
		}
		if finding.FixAction != nil {
			if err := finding.FixAction.Validate(); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateFindingsForCandidate(candidate ProjectSetupCandidate, findingSets ...[]ProjectSetupFinding) error {
	for _, findings := range findingSets {
		for _, finding := range findings {
			if finding.Subject.CandidatePath != "" && !candidatePathExists(candidate, finding.Subject.CandidatePath) {
				return fmt.Errorf("finding subject candidate path %q does not exist", finding.Subject.CandidatePath)
			}
			if finding.FixAction != nil && finding.FixAction.Type == FixActionFocusField {
				if finding.Subject.CandidatePath == "" || finding.FixAction.Target != finding.Subject.CandidatePath || !candidatePathExists(candidate, finding.FixAction.Target) {
					return fmt.Errorf("focus field target %q does not identify the finding subject", finding.FixAction.Target)
				}
			}
		}
	}
	return nil
}

func validCheckGroup(group ProjectSetupCheckGroup) bool {
	switch group {
	case ProjectSetupCheckSolutionPackage, ProjectSetupCheckRepositoryExternal, ProjectSetupCheckAgentSlots, ProjectSetupCheckRuntimeWorkspaceEnvironment, ProjectSetupCheckAuthorizationGovernance:
		return true
	default:
		return false
	}
}

func (action FixAction) Validate() error {
	switch action.Type {
	case FixActionFocusField:
		if !validCandidatePath(action.Target) {
			return fmt.Errorf("invalid focus field target %q", action.Target)
		}
	case FixActionOpenRoute:
		if action.Target != RouteAgentCenter && action.Target != RouteCredentialBindings && action.Target != RouteProjectSetup {
			return fmt.Errorf("invalid open route target %q", action.Target)
		}
	case FixActionRerunCheck:
		if action.Target != RerunProjectSetupEvaluation {
			return fmt.Errorf("invalid rerun check target %q", action.Target)
		}
	default:
		return fmt.Errorf("invalid fix action type %q", action.Type)
	}
	return nil
}

func validCandidatePath(path string) bool {
	parts := strings.Split(path, ".")
	for _, part := range parts {
		if part == "" {
			return false
		}
	}
	if len(parts) == 1 {
		switch parts[0] {
		case "project_id", "solution", "project_parameters", "repositories", "external_targets", "responsibility_bindings", "policy_overrides":
			return true
		}
	}
	if len(parts) == 2 && parts[0] == "solution" {
		return oneOf(parts[1], "solution_id", "version", "artifact_digest")
	}
	if len(parts) == 1 || len(parts) == 2 {
		if validIndexedCandidateSegment(parts[0], "repositories") {
			return len(parts) == 1 || oneOf(parts[1], "binding_key", "repository_uri", "access", "credential_binding_id")
		}
		if validIndexedCandidateSegment(parts[0], "external_targets") {
			return len(parts) == 1 || oneOf(parts[1], "binding_key", "display", "target_type", "environment", "endpoint_uri", "credential_binding_id")
		}
		if validIndexedCandidateSegment(parts[0], "responsibility_bindings") {
			return len(parts) == 1 || parts[1] == "candidate_agent_definition_ids"
		}
	}
	if len(parts) == 3 && validIndexedCandidateSegment(parts[0], "external_targets") {
		return parts[1] == "display" && parts[2] == "name"
	}
	return false
}

func candidatePathExists(candidate ProjectSetupCandidate, path string) bool {
	if !validCandidatePath(path) {
		return false
	}
	if strings.HasPrefix(path, "repositories[") {
		return indexedCandidatePathExists(path, "repositories", len(candidate.Repositories))
	}
	if strings.HasPrefix(path, "external_targets[") {
		return indexedCandidatePathExists(path, "external_targets", len(candidate.ExternalTargets))
	}
	if strings.HasPrefix(path, "responsibility_bindings[") {
		return indexedCandidatePathExists(path, "responsibility_bindings", len(candidate.ResponsibilityBindings))
	}
	return true
}

func indexedCandidatePathExists(path, collection string, length int) bool {
	segment := strings.Split(path, ".")[0]
	index := strings.TrimSuffix(strings.TrimPrefix(segment, collection+"["), "]")
	value, err := strconv.ParseUint(index, 10, 0)
	return err == nil && value <= uint64(^uint(0)>>1) && value < uint64(length)
}

func validIndexedCandidateSegment(segment, collection string) bool {
	prefix := collection + "["
	if !strings.HasPrefix(segment, prefix) || !strings.HasSuffix(segment, "]") {
		return false
	}
	index := strings.TrimSuffix(strings.TrimPrefix(segment, prefix), "]")
	if index == "" {
		return false
	}
	for _, character := range index {
		if character < '0' || character > '9' {
			return false
		}
	}
	value, err := strconv.ParseUint(index, 10, 0)
	return err == nil && value <= uint64(^uint(0)>>1)
}

func oneOf(value string, allowed ...string) bool {
	for _, candidate := range allowed {
		if value == candidate {
			return true
		}
	}
	return false
}

// Validate confirms an adoption request has the exact candidate and CAS facts.
func (request AdoptProjectSolutionSetupRequest) Validate() error {
	if request.ProjectID == "" {
		return required("project_id")
	}
	if err := request.Candidate.Validate(); err != nil {
		return err
	}
	if request.Candidate.ProjectID != request.ProjectID {
		return fmt.Errorf("candidate project_id does not match request project_id")
	}
	if err := validateSHA256Digest("candidate_digest", request.CandidateDigest); err != nil {
		return err
	}
	if request.IdempotencyKey == "" {
		return required("idempotency_key")
	}
	return nil
}

// AdoptProjectSolutionSetup validates the current candidate, then returns a
// replacement Project value. Storage and audit ownership remain outside this
// package; callers persist the returned value atomically with their AuditEvent.
func AdoptProjectSolutionSetup(ctx context.Context, current Project, request AdoptProjectSolutionSetupRequest, validator ProjectSetupValidator) (Project, error) {
	if err := request.Validate(); err != nil {
		return Project{}, err
	}
	if current.ProjectID != request.ProjectID {
		return Project{}, fmt.Errorf("project_id does not match current project")
	}
	if current.ArchivedAt != nil {
		return Project{}, ErrProjectArchived
	}
	if current.Revision != request.ExpectedRevision {
		return Project{}, ErrRevisionConflict
	}
	if current.Revision == ^uint64(0) {
		return Project{}, ErrRevisionExhausted
	}
	if validator == nil {
		return Project{}, errors.New("project setup validator is required")
	}
	candidate := cloneProjectSetupCandidate(request.Candidate)
	result, err := validator.Evaluate(ctx, cloneProjectSetupCandidate(candidate))
	if err != nil {
		return Project{}, fmt.Errorf("evaluate project setup: %w", err)
	}
	if err := result.ValidateForCandidate(candidate); err != nil {
		return Project{}, fmt.Errorf("invalid project setup evaluation: %w", err)
	}
	if result.CandidateDigest != request.CandidateDigest {
		return Project{}, ErrEvaluationMismatch
	}
	if result.Readiness != ProjectSetupReadinessReady {
		return Project{}, ErrCandidateNotReady
	}
	canonicalNormalizedValues, err := result.NormalizedValues.Canonicalize()
	if err != nil {
		return Project{}, fmt.Errorf("canonicalize normalized project setup values: %w", err)
	}
	result.NormalizedValues = canonicalNormalizedValues

	adopted := current
	adopted.SolutionSetup = solutionSetupFromCandidate(candidate, result)
	adopted.Revision++
	adopted.ConfigurationDigest = adopted.SolutionSetup.ConfigurationDigest()
	if adopted.ConfigurationDigest == "" {
		return Project{}, errors.New("compute configuration digest")
	}
	return adopted, nil
}

func solutionSetupFromCandidate(_ ProjectSetupCandidate, result ProjectSetupValidationResult) *ProjectSolutionSetup {
	return &ProjectSolutionSetup{
		Solution:            result.Solution,
		ProjectParameters:   cloneProjectParameters(result.NormalizedValues.ProjectParameters),
		Repositories:        canonicalRepositoryBindings(result.Repositories),
		EnvironmentBindings: cloneEnvironmentBindings(result.EnvironmentBindings),
		ExternalTargets:     canonicalExternalTargetBindings(result.ExternalTargets),
		TeamBindings:        canonicalizeResponsibilityBindings(normalizedTeamBindings(result.TeamBindings)),
		PolicyOverrides:     clonePolicyOverrides(result.NormalizedValues.PolicyOverrides),
	}
}

func cloneProjectSetupCandidate(candidate ProjectSetupCandidate) ProjectSetupCandidate {
	cloned := candidate
	cloned.ProjectParameters = cloneProjectParameters(candidate.ProjectParameters)
	cloned.Repositories = append([]RepositoryCandidate(nil), candidate.Repositories...)
	cloned.ExternalTargets = append([]ExternalTargetCandidate(nil), candidate.ExternalTargets...)
	cloned.ResponsibilityBindings = cloneResponsibilitySelections(candidate.ResponsibilityBindings)
	cloned.PolicyOverrides = clonePolicyOverrides(candidate.PolicyOverrides)
	return cloned
}

func canonicalRepositoryBindings(bindings []RepositoryBinding) []RepositoryBinding {
	canonical := append([]RepositoryBinding(nil), bindings...)
	for index := range canonical {
		canonical[index].RepositoryURI, _ = NormalizeRepositoryURI(canonical[index].RepositoryURI)
	}
	return canonical
}

func canonicalExternalTargetBindings(bindings []ExternalTargetBinding) []ExternalTargetBinding {
	canonical := cloneExternalTargetBindings(bindings)
	for index := range canonical {
		canonical[index].EndpointURI, _ = NormalizeTargetURI(canonical[index].EndpointURI)
	}
	return canonical
}

func cloneResponsibilitySelections(bindings []ResponsibilitySelection) []ResponsibilitySelection {
	cloned := make([]ResponsibilitySelection, len(bindings))
	for index, binding := range bindings {
		cloned[index] = binding
		cloned[index].CandidateAgentDefinitionIDs = append([]string(nil), binding.CandidateAgentDefinitionIDs...)
	}
	return cloned
}

func normalizedTeamBindings(bindings []EvaluatedTeamBinding) []ResponsibilityBinding {
	normalized := make([]ResponsibilityBinding, len(bindings))
	for index, binding := range bindings {
		normalized[index] = binding.ResponsibilityBinding()
		normalized[index].CandidateAgentDefinitionIDs = append([]string(nil), binding.CandidateAgentDefinitionIDs...)
		normalized[index].RequiredCapabilities = append([]string(nil), binding.RequiredCapabilities...)
	}
	return normalized
}

func cloneProjectParameters(values ProjectParameters) ProjectParameters {
	if len(values) == 0 {
		return nil
	}
	cloned := make(ProjectParameters, len(values))
	for key, value := range values {
		cloned[key] = append([]byte(nil), value...)
	}
	return cloned
}

func clonePolicyOverrides(values PolicyOverrides) PolicyOverrides {
	if len(values) == 0 {
		return nil
	}
	cloned := make(PolicyOverrides, len(values))
	for key, value := range values {
		cloned[key] = append([]byte(nil), value...)
	}
	return cloned
}

func cloneEnvironmentBindings(bindings []EnvironmentBinding) []EnvironmentBinding {
	cloned := make([]EnvironmentBinding, len(bindings))
	for index, binding := range bindings {
		cloned[index] = binding
		cloned[index].Usages = append([]EnvironmentUsage(nil), binding.Usages...)
	}
	return cloned
}

func cloneExternalTargetBindings(bindings []ExternalTargetBinding) []ExternalTargetBinding {
	return append([]ExternalTargetBinding(nil), bindings...)
}

func cloneResponsibilityBindings(bindings []ResponsibilityBinding) []ResponsibilityBinding {
	cloned := make([]ResponsibilityBinding, len(bindings))
	for index, binding := range bindings {
		cloned[index] = binding
		cloned[index].CandidateAgentDefinitionIDs = append([]string(nil), binding.CandidateAgentDefinitionIDs...)
		cloned[index].RequiredCapabilities = append([]string(nil), binding.RequiredCapabilities...)
	}
	return cloned
}

// Validate confirms a successor context refers to an approved impact path.
func (context SuccessorContext) Validate() error {
	if context.PredecessorWorkflowName == "" {
		return required("successor_context.predecessor_workflow_name")
	}
	if context.ImpactAnalysisArtifactID == "" {
		return required("successor_context.impact_analysis_artifact_id")
	}
	if context.ApprovalDecisionID == "" {
		return required("successor_context.approval_decision_id")
	}
	return nil
}

func (goal WorkflowGoal) Validate() error {
	if goal.Title == "" {
		return required("goal.title")
	}
	if goal.Description == "" {
		return required("goal.description")
	}
	return uniqueNonEmpty("goal.known_constraints", goal.KnownConstraints)
}

// Validate confirms every root workflow, including a successor, uses the
// ordinary creation command and its normal CAS and idempotency controls.
func (request CreateWorkflowFromProjectSetupRequest) Validate() error {
	if request.ProjectID == "" {
		return required("project_id")
	}
	if request.ExpectedConfigurationDigest == "" {
		return required("expected_configuration_digest")
	}
	if !artifactDigestPattern.MatchString(request.ExpectedConfigurationDigest) {
		return fmt.Errorf("expected_configuration_digest must be a sha256 digest")
	}
	if err := request.Goal.Validate(); err != nil {
		return err
	}
	if err := uniqueNonEmpty("input_artifact_ids", request.InputArtifactIDs); err != nil {
		return err
	}
	if _, err := canonicalizeRawMessages(request.WorkflowInputs); err != nil {
		return fmt.Errorf("workflow_inputs: %w", err)
	}
	if request.IdempotencyKey == "" {
		return required("idempotency_key")
	}
	if request.SuccessorContext != nil {
		return request.SuccessorContext.Validate()
	}
	return nil
}

func uniqueNonEmpty(field string, values []string) error {
	seen := make(map[string]struct{}, len(values))
	for _, value := range values {
		if value == "" {
			return required(field)
		}
		if _, exists := seen[value]; exists {
			return duplicate(field, value)
		}
		seen[value] = struct{}{}
	}
	return nil
}

func required(field string) error {
	return fmt.Errorf("%s is required", field)
}

func duplicate(field, value string) error {
	return fmt.Errorf("%s contains duplicate value %q", field, value)
}

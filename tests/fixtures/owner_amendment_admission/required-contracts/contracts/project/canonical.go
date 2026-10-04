package project

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
)

// Canonicalize normalizes JSON field values and unordered candidate collections.
// Responsibility selections retain Solution-provided order because the
// evaluator's server-owned slot projection is indexed by that order.
func (candidate ProjectSetupCandidate) Canonicalize() (ProjectSetupCandidate, error) {
	canonical := candidate
	var err error
	if canonical.ProjectParameters, err = canonicalizeRawMessages(candidate.ProjectParameters); err != nil {
		return ProjectSetupCandidate{}, fmt.Errorf("canonicalize project_parameters: %w", err)
	}
	if canonical.PolicyOverrides, err = canonicalizeRawMessages(candidate.PolicyOverrides); err != nil {
		return ProjectSetupCandidate{}, fmt.Errorf("canonicalize policy_overrides: %w", err)
	}
	canonical.Repositories = append([]RepositoryCandidate(nil), candidate.Repositories...)
	sort.Slice(canonical.Repositories, func(left, right int) bool {
		return canonical.Repositories[left].BindingKey < canonical.Repositories[right].BindingKey
	})
	canonical.ExternalTargets = append([]ExternalTargetCandidate(nil), candidate.ExternalTargets...)
	sort.Slice(canonical.ExternalTargets, func(left, right int) bool {
		return canonical.ExternalTargets[left].BindingKey < canonical.ExternalTargets[right].BindingKey
	})
	canonical.ResponsibilityBindings = canonicalizeResponsibilitySelections(candidate.ResponsibilityBindings)
	return canonical, nil
}

func canonicalizeResponsibilitySelections(bindings []ResponsibilitySelection) []ResponsibilitySelection {
	canonical := cloneResponsibilitySelections(bindings)
	for index := range canonical {
		sort.Strings(canonical[index].CandidateAgentDefinitionIDs)
	}
	return canonical
}

// ConfigurationDigest returns a digest of the complete adopted setup, including
// policy- and probe-resolved values that do not belong to the form candidate.
func (setup ProjectSolutionSetup) ConfigurationDigest() string {
	canonical, err := setup.canonicalize()
	if err != nil {
		return ""
	}
	return digest(canonical)
}

func (setup ProjectSolutionSetup) canonicalize() (ProjectSolutionSetup, error) {
	canonical := setup
	var err error
	if canonical.ProjectParameters, err = canonicalizeRawMessages(setup.ProjectParameters); err != nil {
		return ProjectSolutionSetup{}, fmt.Errorf("canonicalize project_parameters: %w", err)
	}
	if canonical.PolicyOverrides, err = canonicalizeRawMessages(setup.PolicyOverrides); err != nil {
		return ProjectSolutionSetup{}, fmt.Errorf("canonicalize policy_overrides: %w", err)
	}
	canonical.Repositories = append([]RepositoryBinding(nil), setup.Repositories...)
	for index := range canonical.Repositories {
		canonicalURI, err := NormalizeRepositoryURI(canonical.Repositories[index].RepositoryURI)
		if err != nil {
			return ProjectSolutionSetup{}, fmt.Errorf("canonicalize repositories[%d].repository_uri: %w", index, err)
		}
		canonical.Repositories[index].RepositoryURI = canonicalURI
	}
	sort.Slice(canonical.Repositories, func(left, right int) bool {
		return canonical.Repositories[left].BindingKey < canonical.Repositories[right].BindingKey
	})
	canonical.EnvironmentBindings = append([]EnvironmentBinding(nil), setup.EnvironmentBindings...)
	sort.Slice(canonical.EnvironmentBindings, func(left, right int) bool {
		return canonical.EnvironmentBindings[left].EnvironmentID < canonical.EnvironmentBindings[right].EnvironmentID
	})
	for index := range canonical.EnvironmentBindings {
		canonical.EnvironmentBindings[index].Usages = append([]EnvironmentUsage(nil), canonical.EnvironmentBindings[index].Usages...)
		sort.Slice(canonical.EnvironmentBindings[index].Usages, func(left, right int) bool {
			return canonical.EnvironmentBindings[index].Usages[left] < canonical.EnvironmentBindings[index].Usages[right]
		})
	}
	canonical.ExternalTargets = append([]ExternalTargetBinding(nil), setup.ExternalTargets...)
	for index := range canonical.ExternalTargets {
		canonicalURI, err := NormalizeTargetURI(canonical.ExternalTargets[index].EndpointURI)
		if err != nil {
			return ProjectSolutionSetup{}, fmt.Errorf("canonicalize external_targets[%d].endpoint_uri: %w", index, err)
		}
		canonical.ExternalTargets[index].EndpointURI = canonicalURI
	}
	sort.Slice(canonical.ExternalTargets, func(left, right int) bool {
		return canonical.ExternalTargets[left].BindingKey < canonical.ExternalTargets[right].BindingKey
	})
	canonical.TeamBindings = canonicalizeResponsibilityBindings(setup.TeamBindings)
	return canonical, nil
}

func canonicalizeResponsibilityBindings(bindings []ResponsibilityBinding) []ResponsibilityBinding {
	canonical := cloneResponsibilityBindings(bindings)
	for index := range canonical {
		sort.Strings(canonical[index].CandidateAgentDefinitionIDs)
		sort.Strings(canonical[index].RequiredCapabilities)
	}
	sort.Slice(canonical, func(left, right int) bool {
		return canonical[left].ResponsibilityID < canonical[right].ResponsibilityID
	})
	return canonical
}

func canonicalizeRawMessages(values map[string]json.RawMessage) (map[string]json.RawMessage, error) {
	if len(values) == 0 {
		return nil, nil
	}
	canonical := make(map[string]json.RawMessage, len(values))
	for key, raw := range values {
		if !json.Valid(raw) {
			return nil, fmt.Errorf("%q is not valid JSON", key)
		}
		if err := rejectDuplicateObjectMembers(raw); err != nil {
			return nil, fmt.Errorf("%q is ambiguous JSON: %w", key, err)
		}
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		var value any
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		normalized, err := json.Marshal(value)
		if err != nil {
			return nil, err
		}
		canonical[key] = normalized
	}
	return canonical, nil
}

func rejectDuplicateObjectMembers(raw json.RawMessage) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := consumeJSONValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values")
		}
		return err
	}
	return nil
}

func consumeJSONValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, isDelimiter := token.(json.Delim)
	if !isDelimiter {
		return nil
	}
	switch delimiter {
	case '{':
		keys := make(map[string]struct{})
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok {
				return fmt.Errorf("invalid object key")
			}
			if _, exists := keys[key]; exists {
				return fmt.Errorf("duplicate object member %q", key)
			}
			keys[key] = struct{}{}
			if err := consumeJSONValue(decoder); err != nil {
				return err
			}
		}
		_, err = decoder.Token()
		return err
	case '[':
		for decoder.More() {
			if err := consumeJSONValue(decoder); err != nil {
				return err
			}
		}
		_, err = decoder.Token()
		return err
	default:
		return fmt.Errorf("unexpected JSON delimiter %q", delimiter)
	}
}

func digest(value any) string {
	payload, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(payload)
	return "sha256:" + hex.EncodeToString(sum[:])
}

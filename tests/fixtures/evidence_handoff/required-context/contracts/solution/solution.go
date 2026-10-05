// Package solution defines the immutable, declarative contents of a Solution package.
package solution

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"golang.org/x/mod/semver"
)

// LocalizedText holds locale-keyed user-facing text.
type LocalizedText map[string]string

// Display is the localized name and description required by semantic objects.
type Display struct {
	Name        LocalizedText `json:"name"`
	Description LocalizedText `json:"description"`
}

// ExactPackageRef pins an independently installable package release.
type ExactPackageRef struct {
	ComponentID    string `json:"componentId"`
	Version        string `json:"version"`
	ArtifactDigest string `json:"artifactDigest"`
}

// ContentKind distinguishes package-local content without introducing a catch-all content reference field.
type ContentKind string

const (
	ContentKindDataContract        ContentKind = "data_contract"
	ContentKindWorkUnitContract    ContentKind = "work_unit_contract"
	ContentKindHarnessDefinition   ContentKind = "harness_definition"
	ContentKindVerificationFixture ContentKind = "verification_fixture"
)

// PackageContent identifies content published inside the same Solution package.
type PackageContent struct {
	Kind              ContentKind        `json:"kind"`
	ContentID         string             `json:"contentId"`
	ContentDigest     string             `json:"contentDigest"`
	PackagePath       string             `json:"packagePath"`
	WorkUnitContract  *WorkUnitContract  `json:"workUnitContract,omitempty"`
	HarnessDefinition *HarnessDefinition `json:"harnessDefinition,omitempty"`
}

// LocalContentReference identifies package-local content without copying its typed payload.
type LocalContentReference struct {
	ContentID     string `json:"contentId"`
	ContentDigest string `json:"contentDigest"`
	PackagePath   string `json:"packagePath"`
}

// ContentReference selects either package-local content or an exact external package.
type ContentReference struct {
	Local    *LocalContentReference `json:"local,omitempty"`
	External *ExactPackageRef       `json:"external,omitempty"`
	Resolved *ResolvedContent       `json:"-"`
}

// ResolvedContent supplies the typed result of resolving an external exact reference.
type ResolvedContent struct {
	WorkUnitContract  *WorkUnitContract
	HarnessDefinition *HarnessDefinition
}

// FieldType is a Generic Form Engine field type supported by Solution packages.
type FieldType string

const (
	FieldTypeText                    FieldType = "text"
	FieldTypeNumber                  FieldType = "number"
	FieldTypeBoolean                 FieldType = "boolean"
	FieldTypeChoice                  FieldType = "choice"
	FieldTypeRepositoryBinding       FieldType = "repository_binding"
	FieldTypeCredentialBinding       FieldType = "credential_binding"
	FieldTypeDeploymentTargetBinding FieldType = "deployment_target_binding"
	FieldTypeArtifactInput           FieldType = "artifact_input"
)

// FormOption is a documented value available to a choice field.
type FormOption struct {
	Value   string  `json:"value"`
	Display Display `json:"display"`
}

// FormValidation contains only static, type-specific validation rules.
type FormValidation struct {
	Minimum   *float64 `json:"minimum,omitempty"`
	Maximum   *float64 `json:"maximum,omitempty"`
	MinLength *int     `json:"minLength,omitempty"`
	MaxLength *int     `json:"maxLength,omitempty"`
	Pattern   string   `json:"pattern,omitempty"`
}

// FormField is one statically declared Project or Workflow input.
type FormField struct {
	FieldKey     string          `json:"fieldKey"`
	Display      Display         `json:"display"`
	FieldType    FieldType       `json:"fieldType"`
	Required     bool            `json:"required"`
	DefaultValue json.RawMessage `json:"defaultValue,omitempty"`
	Validation   *FormValidation `json:"validation,omitempty"`
	Options      []FormOption    `json:"options,omitempty"`
}

// FormSchema is a top-level declarative input schema.
type FormSchema struct {
	Fields []FormField `json:"fields"`
}

// ValidateFormSchema validates one present authoring schema without requiring
// a complete SolutionPackage.
func ValidateFormSchema(schema FormSchema) error {
	return schema.validate("form schema")
}

// SetupRequirement controls whether a responsibility must be configured at Project Setup.
type SetupRequirement string

const (
	SetupRequirementRequired    SetupRequirement = "required"
	SetupRequirementRecommended SetupRequirement = "recommended"
)

// ResponsibilitySlot describes a Project Setup responsibility without binding an Agent.
type ResponsibilitySlot struct {
	SlotKey                   string            `json:"slotKey"`
	Display                   Display           `json:"display"`
	SetupRequirement          SetupRequirement  `json:"setupRequirement"`
	CapabilityRequirements    []string          `json:"capabilityRequirements,omitempty"`
	RecommendedAgentTemplates []ExactPackageRef `json:"recommendedAgentTemplates,omitempty"`
}

// ExecutionKind is the single primary execution mode of a baseline node.
type ExecutionKind string

const (
	ExecutionKindExecutor      ExecutionKind = "executor"
	ExecutionKindChildWorkflow ExecutionKind = "child-workflow"
)

// ExecutorExecution declares the capabilities needed for executor execution.
type ExecutorExecution struct {
	CapabilityRequirements []string `json:"capabilityRequirements"`
}

// ChildWorkflowExecution pins the Solution used by a child workflow.
type ChildWorkflowExecution struct {
	Solution ExactPackageRef `json:"solution"`
}

// Execution is a tagged union for the node's sole primary execution mechanism.
type Execution struct {
	Kind          ExecutionKind           `json:"kind"`
	Executor      *ExecutorExecution      `json:"executor,omitempty"`
	ChildWorkflow *ChildWorkflowExecution `json:"childWorkflow,omitempty"`
}

// ResponsibilityRequirement declares the responsibility and minimum capability set for a node.
type ResponsibilityRequirement struct {
	SlotKey                string   `json:"slotKey"`
	CapabilityRequirements []string `json:"capabilityRequirements"`
}

// ContractSlot is a typed WorkUnit input or output slot.
type ContractSlot struct {
	SlotKey      string           `json:"slotKey"`
	Display      Display          `json:"display"`
	Required     bool             `json:"required"`
	DataContract ContentReference `json:"dataContract"`
}

// WorkUnitContract is package-local typed content describing work inputs and outputs.
type WorkUnitContract struct {
	Display     Display        `json:"display"`
	InputSlots  []ContractSlot `json:"inputSlots,omitempty"`
	OutputSlots []ContractSlot `json:"outputSlots"`
}

// ValidateWorkUnitContract validates one present Contract without requiring a
// complete SolutionPackage.
func ValidateWorkUnitContract(contract WorkUnitContract) error {
	return contract.validate(nil)
}

// InputBinding maps a confirmed upstream output into a node input slot.
type InputBinding struct {
	FromNodeID     string `json:"fromNodeId"`
	FromOutputSlot string `json:"fromOutputSlot"`
	ToInputSlot    string `json:"toInputSlot"`
}

// RequirementKind identifies the controlled condition that may activate a node.
type RequirementKind string

const (
	RequirementKindGate     RequirementKind = "gate"
	RequirementKindDecision RequirementKind = "decision"
)

// ActivationRequirement selects one declared Gate or Decision outcome.
type ActivationRequirement struct {
	FromNodeID    string          `json:"fromNodeId"`
	RequirementID string          `json:"requirementId"`
	Kind          RequirementKind `json:"kind"`
	Outcome       string          `json:"outcome"`
}

// Activation contains the typed conditions required before a node becomes Ready.
type Activation struct {
	Requirements []ActivationRequirement `json:"requirements"`
}

// OutcomeDisposition determines whether a Gate or Decision outcome continues or terminates a fixed DAG path.
type OutcomeDisposition string

const (
	OutcomeDispositionContinuation       OutcomeDisposition = "continuation"
	OutcomeDispositionTerminalSuccess    OutcomeDisposition = "terminal_success"
	OutcomeDispositionTerminalNonSuccess OutcomeDisposition = "terminal_non_success"
)

// OutcomeDefinition is one named Gate or Decision outcome with an explicit fixed-DAG disposition.
type OutcomeDefinition struct {
	Value       string             `json:"value"`
	Disposition OutcomeDisposition `json:"disposition"`
}

// OutcomeRequirement is a Gate or Decision requirement owned by a HarnessDefinition.
type OutcomeRequirement struct {
	RequirementID string              `json:"requirementId"`
	Outcomes      []OutcomeDefinition `json:"outcomes"`
}

// HarnessDefinition owns Gate and Decision requirements; it never owns primary execution.
type HarnessDefinition struct {
	GateRequirements     []OutcomeRequirement `json:"gateRequirements,omitempty"`
	DecisionRequirements []OutcomeRequirement `json:"decisionRequirements,omitempty"`
}

// ValidateHarnessDefinition validates one present HarnessDefinition without
// requiring a complete SolutionPackage.
func ValidateHarnessDefinition(harness HarnessDefinition) error {
	return harness.validate()
}

// PlanNodeTemplate is one ordinary DAG node in a BaselinePlanTemplate.
type PlanNodeTemplate struct {
	NodeID                    string                    `json:"nodeId"`
	Display                   Display                   `json:"display"`
	WorkUnitContract          ContentReference          `json:"workUnitContract"`
	ResponsibilityRequirement ResponsibilityRequirement `json:"responsibilityRequirement"`
	InputBindings             []InputBinding            `json:"inputBindings,omitempty"`
	Activation                *Activation               `json:"activation,omitempty"`
	Execution                 Execution                 `json:"execution"`
	Harness                   ContentReference          `json:"harness"`
}

// PlanEdge is a hard dependency from one baseline node to another.
type PlanEdge struct {
	FromNodeID string `json:"fromNodeId"`
	ToNodeID   string `json:"toNodeId"`
}

// BaselinePlanTemplate is a package-local, immutable DAG template.
type BaselinePlanTemplate struct {
	TemplateID    string             `json:"templateId"`
	ContentDigest string             `json:"contentDigest"`
	Display       Display            `json:"display"`
	Nodes         []PlanNodeTemplate `json:"nodes"`
	Edges         []PlanEdge         `json:"edges"`
}

// DeclarativeExperience contains only supported read-model and presentation declarations.
type DeclarativeExperience struct {
	Views       []ExperienceView     `json:"views,omitempty"`
	Forms       []ExperienceForm     `json:"forms,omitempty"`
	Layouts     []ExperienceLayout   `json:"layouts,omitempty"`
	Renderers   []ExperienceRenderer `json:"renderers,omitempty"`
	Terminology map[string]Display   `json:"terminology,omitempty"`
}

// ExperienceView declares a safe, named domain view.
type ExperienceView struct {
	ID      string  `json:"id"`
	Display Display `json:"display"`
}

// ExperienceForm declares a safe, named domain form.
type ExperienceForm struct {
	ID      string  `json:"id"`
	Display Display `json:"display"`
}

// ExperienceLayout declares a safe, named supported layout.
type ExperienceLayout struct {
	ID      string  `json:"id"`
	Display Display `json:"display"`
}

// ExperienceRenderer declares a safe, named built-in renderer.
type ExperienceRenderer struct {
	ID      string  `json:"id"`
	Display Display `json:"display"`
}

// SolutionPackage publishes schemas, responsibility slots, a baseline plan, and exact references.
type SolutionPackage struct {
	SolutionID           string                 `json:"solutionId"`
	Version              string                 `json:"version"`
	ArtifactDigest       string                 `json:"artifactDigest"`
	Display              Display                `json:"display"`
	ProjectSetupSchema   FormSchema             `json:"projectSetupSchema"`
	ResponsibilitySlots  []ResponsibilitySlot   `json:"responsibilitySlots"`
	WorkflowInputSchema  FormSchema             `json:"workflowInputSchema"`
	BaselinePlanTemplate BaselinePlanTemplate   `json:"baselinePlanTemplate"`
	PackageContents      []PackageContent       `json:"packageContents,omitempty"`
	PackageDependencies  []ExactPackageRef      `json:"packageDependencies,omitempty"`
	VerificationFixtures []ContentReference     `json:"verificationFixtures"`
	Experience           *DeclarativeExperience `json:"experience,omitempty"`
}

// Validate rejects incomplete, unpinned, or structurally unsafe Solution package definitions.
func (pkg SolutionPackage) Validate() error {
	if isBlank(pkg.SolutionID) || !isVersion(pkg.Version) || !isDigest(pkg.ArtifactDigest) {
		return fmt.Errorf("solution identity, exact version, and artifact digest are required")
	}
	if err := pkg.Display.validate("solution display"); err != nil {
		return err
	}
	if err := pkg.ProjectSetupSchema.validate("project setup schema"); err != nil {
		return err
	}
	if err := pkg.WorkflowInputSchema.validate("workflow input schema"); err != nil {
		return err
	}
	if err := validateSlots(pkg.ResponsibilitySlots); err != nil {
		return err
	}
	contents, err := contentIndex(pkg.PackageContents)
	if err != nil {
		return err
	}
	if err := validateContents(contents); err != nil {
		return err
	}
	if err := pkg.BaselinePlanTemplate.validateWithDefinitions(contents, slotIndex(pkg.ResponsibilitySlots)); err != nil {
		return err
	}
	for _, dependency := range pkg.PackageDependencies {
		if err := dependency.validate(); err != nil {
			return fmt.Errorf("package dependency: %w", err)
		}
	}
	if len(pkg.VerificationFixtures) == 0 {
		return fmt.Errorf("at least one verification fixture is required")
	}
	for _, fixture := range pkg.VerificationFixtures {
		if fixture.Local == nil || fixture.External != nil {
			return fmt.Errorf("verification fixtures must be package-local content")
		}
		if err := fixture.validateLocal(contents, ContentKindVerificationFixture); err != nil {
			return fmt.Errorf("verification fixture: %w", err)
		}
	}
	if pkg.Experience != nil {
		if err := pkg.Experience.validate(); err != nil {
			return fmt.Errorf("experience: %w", err)
		}
	}
	return nil
}

// HasDAGOrchestrationNode reports whether the template has the explicit executor node required for dynamic planning.
func (template BaselinePlanTemplate) HasDAGOrchestrationNode() bool {
	for _, node := range template.Nodes {
		if node.Execution.Kind == ExecutionKindExecutor && node.Execution.Executor != nil && contains(node.Execution.Executor.CapabilityRequirements, "orchestration.dag") {
			return true
		}
	}
	return false
}

func (display Display) validate(subject string) error {
	if len(display.Name) == 0 || len(display.Description) == 0 {
		return fmt.Errorf("%s requires localized name and description", subject)
	}
	for locale, value := range display.Name {
		if isBlank(locale) || isBlank(value) {
			return fmt.Errorf("%s has blank localized name", subject)
		}
	}
	for locale, value := range display.Description {
		if isBlank(locale) || isBlank(value) {
			return fmt.Errorf("%s has blank localized description", subject)
		}
	}
	return nil
}

func (schema FormSchema) validate(subject string) error {
	keys := make(map[string]struct{}, len(schema.Fields))
	for _, field := range schema.Fields {
		if isBlank(field.FieldKey) {
			return fmt.Errorf("%s has blank field key", subject)
		}
		if _, exists := keys[field.FieldKey]; exists {
			return fmt.Errorf("%s has duplicate field key %q", subject, field.FieldKey)
		}
		keys[field.FieldKey] = struct{}{}
		if err := field.Display.validate("field " + field.FieldKey); err != nil {
			return err
		}
		if err := field.validate(); err != nil {
			return err
		}
	}
	return nil
}

func (field FormField) validate() error {
	if !isFieldType(field.FieldType) {
		return fmt.Errorf("field %q has unsupported type %q", field.FieldKey, field.FieldType)
	}
	values := make(map[string]struct{}, len(field.Options))
	if field.FieldType == FieldTypeChoice && len(field.Options) == 0 {
		return fmt.Errorf("choice field %q requires options", field.FieldKey)
	}
	if field.FieldType != FieldTypeChoice && len(field.Options) != 0 {
		return fmt.Errorf("non-choice field %q cannot define options", field.FieldKey)
	}
	for _, option := range field.Options {
		if isBlank(option.Value) {
			return fmt.Errorf("choice field %q has blank option value", field.FieldKey)
		}
		if _, exists := values[option.Value]; exists {
			return fmt.Errorf("choice field %q has duplicate option value %q", field.FieldKey, option.Value)
		}
		values[option.Value] = struct{}{}
		if err := option.Display.validate("choice option " + option.Value); err != nil {
			return err
		}
	}
	if err := field.Validation.validate(field.FieldType, field.FieldKey); err != nil {
		return err
	}
	return field.validateDefault(values)
}

func (validation *FormValidation) validate(fieldType FieldType, fieldKey string) error {
	if validation == nil {
		return nil
	}
	if validation.Minimum != nil && validation.Maximum != nil && *validation.Minimum > *validation.Maximum {
		return fmt.Errorf("field %q has minimum greater than maximum", fieldKey)
	}
	if validation.MinLength != nil && validation.MaxLength != nil && *validation.MinLength > *validation.MaxLength {
		return fmt.Errorf("field %q has minLength greater than maxLength", fieldKey)
	}
	if (validation.Minimum != nil || validation.Maximum != nil) && fieldType != FieldTypeNumber {
		return fmt.Errorf("field %q uses numeric validation with non-number type", fieldKey)
	}
	if (validation.MinLength != nil || validation.MaxLength != nil || validation.Pattern != "") && fieldType != FieldTypeText {
		return fmt.Errorf("field %q uses text validation with non-text type", fieldKey)
	}
	if validation.Pattern != "" {
		if _, err := regexp.Compile(validation.Pattern); err != nil {
			return fmt.Errorf("field %q has invalid pattern: %w", fieldKey, err)
		}
	}
	return nil
}

func (field FormField) validateDefault(options map[string]struct{}) error {
	if len(field.DefaultValue) == 0 {
		return nil
	}
	var value any
	if err := json.Unmarshal(field.DefaultValue, &value); err != nil {
		return fmt.Errorf("field %q has invalid default value: %w", field.FieldKey, err)
	}
	switch field.FieldType {
	case FieldTypeText:
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("field %q text default must be a string", field.FieldKey)
		}
		if field.Validation != nil {
			if field.Validation.MinLength != nil && len(text) < *field.Validation.MinLength || field.Validation.MaxLength != nil && len(text) > *field.Validation.MaxLength {
				return fmt.Errorf("field %q default violates text length validation", field.FieldKey)
			}
			if field.Validation.Pattern != "" {
				pattern := regexp.MustCompile(field.Validation.Pattern)
				if !pattern.MatchString(text) {
					return fmt.Errorf("field %q default violates pattern", field.FieldKey)
				}
			}
		}
	case FieldTypeNumber:
		number, ok := value.(float64)
		if !ok {
			return fmt.Errorf("field %q number default must be a number", field.FieldKey)
		}
		if field.Validation != nil && ((field.Validation.Minimum != nil && number < *field.Validation.Minimum) || (field.Validation.Maximum != nil && number > *field.Validation.Maximum)) {
			return fmt.Errorf("field %q default violates numeric validation", field.FieldKey)
		}
	case FieldTypeBoolean:
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("field %q boolean default must be a boolean", field.FieldKey)
		}
	case FieldTypeChoice:
		choice, ok := value.(string)
		if !ok {
			return fmt.Errorf("field %q choice default must be a string", field.FieldKey)
		}
		if _, exists := options[choice]; !exists {
			return fmt.Errorf("field %q default is not a declared choice", field.FieldKey)
		}
	default:
		return fmt.Errorf("field %q resource types cannot declare defaults", field.FieldKey)
	}
	return nil
}

func validateSlots(slots []ResponsibilitySlot) error {
	keys := make(map[string]struct{}, len(slots))
	for _, slot := range slots {
		if isBlank(slot.SlotKey) || (slot.SetupRequirement != SetupRequirementRequired && slot.SetupRequirement != SetupRequirementRecommended) {
			return fmt.Errorf("responsibility slot requires a key and supported setup requirement")
		}
		if _, exists := keys[slot.SlotKey]; exists {
			return fmt.Errorf("duplicate responsibility slot %q", slot.SlotKey)
		}
		keys[slot.SlotKey] = struct{}{}
		if err := slot.Display.validate("responsibility slot " + slot.SlotKey); err != nil {
			return err
		}
		for _, capability := range slot.CapabilityRequirements {
			if isBlank(capability) {
				return fmt.Errorf("responsibility slot %q has blank capability", slot.SlotKey)
			}
		}
		for _, template := range slot.RecommendedAgentTemplates {
			if err := template.validate(); err != nil {
				return fmt.Errorf("responsibility slot %q template: %w", slot.SlotKey, err)
			}
		}
	}
	return nil
}

func (template BaselinePlanTemplate) validate() error {
	return template.validateWithDefinitions(nil, nil)
}

func (template BaselinePlanTemplate) validateWithDefinitions(contents map[string]PackageContent, slots map[string]ResponsibilitySlot) error {
	if isBlank(template.TemplateID) || !isDigest(template.ContentDigest) || len(template.Nodes) == 0 {
		return fmt.Errorf("baseline template requires id, digest, and nodes")
	}
	if err := template.Display.validate("baseline template display"); err != nil {
		return err
	}
	nodes := make(map[string]PlanNodeTemplate, len(template.Nodes))
	for _, node := range template.Nodes {
		if isBlank(node.NodeID) {
			return fmt.Errorf("baseline template has blank node id")
		}
		if _, exists := nodes[node.NodeID]; exists {
			return fmt.Errorf("baseline template has duplicate node %q", node.NodeID)
		}
		nodes[node.NodeID] = node
	}
	dependencies := make(map[string][]string, len(nodes))
	for _, edge := range template.Edges {
		if edge.FromNodeID == edge.ToNodeID || nodes[edge.FromNodeID].NodeID == "" || nodes[edge.ToNodeID].NodeID == "" {
			return fmt.Errorf("baseline template has invalid edge %q -> %q", edge.FromNodeID, edge.ToNodeID)
		}
		dependencies[edge.ToNodeID] = append(dependencies[edge.ToNodeID], edge.FromNodeID)
	}
	if hasCycle(dependencies) {
		return fmt.Errorf("baseline template dependencies contain a cycle")
	}
	for _, node := range template.Nodes {
		if err := node.validateWithDefinitions(contents, slots); err != nil {
			return fmt.Errorf("baseline node %q: %w", node.NodeID, err)
		}
		if node.Activation != nil {
			if err := node.Activation.validate(node.NodeID, nodes, dependencies, contents); err != nil {
				return fmt.Errorf("baseline node %q activation: %w", node.NodeID, err)
			}
		}
		for _, binding := range node.InputBindings {
			if isBlank(binding.FromNodeID) || isBlank(binding.FromOutputSlot) || isBlank(binding.ToInputSlot) || !isUpstream(binding.FromNodeID, node.NodeID, dependencies) {
				return fmt.Errorf("input binding must reference an upstream dependency")
			}
			if contents != nil {
				if err := validateBinding(node, nodes[binding.FromNodeID], binding, contents); err != nil {
					return err
				}
			}
		}
	}
	if contents != nil {
		if err := validateBranchClosure(nodes, dependencies, contents); err != nil {
			return err
		}
	}
	return nil
}

func (node PlanNodeTemplate) validate() error {
	return node.validateWithDefinitions(nil, nil)
}

func (node PlanNodeTemplate) validateWithDefinitions(contents map[string]PackageContent, slots map[string]ResponsibilitySlot) error {
	if err := node.Display.validate("baseline node display"); err != nil {
		return err
	}
	if err := node.WorkUnitContract.validateLocal(contents, ContentKindWorkUnitContract); err != nil {
		return fmt.Errorf("work unit contract: %w", err)
	}
	if isBlank(node.ResponsibilityRequirement.SlotKey) || len(node.ResponsibilityRequirement.CapabilityRequirements) == 0 || hasBlank(node.ResponsibilityRequirement.CapabilityRequirements) {
		return fmt.Errorf("responsibility requirement requires a slot and capability")
	}
	if err := node.Execution.validate(); err != nil {
		return err
	}
	if err := node.Harness.validateLocal(contents, ContentKindHarnessDefinition); err != nil {
		return fmt.Errorf("harness: %w", err)
	}
	if contents != nil {
		if _, err := resolveWorkUnitContract(node.WorkUnitContract, contents); err != nil {
			return err
		}
		if _, err := resolveHarnessDefinition(node.Harness, contents); err != nil {
			return err
		}
	}
	if slots != nil {
		slot, exists := slots[node.ResponsibilityRequirement.SlotKey]
		if !exists {
			return fmt.Errorf("responsibility requirement references unknown slot %q", node.ResponsibilityRequirement.SlotKey)
		}
		if !includesAll(node.ResponsibilityRequirement.CapabilityRequirements, slot.CapabilityRequirements) {
			return fmt.Errorf("responsibility requirement weakens slot %q base capabilities", slot.SlotKey)
		}
	}
	return nil
}

func (execution Execution) validate() error {
	switch execution.Kind {
	case ExecutionKindExecutor:
		if execution.Executor == nil || execution.ChildWorkflow != nil || len(execution.Executor.CapabilityRequirements) == 0 || hasBlank(execution.Executor.CapabilityRequirements) {
			return fmt.Errorf("executor execution requires only executor capabilities")
		}
	case ExecutionKindChildWorkflow:
		if execution.Executor != nil || execution.ChildWorkflow == nil {
			return fmt.Errorf("child-workflow execution requires only child workflow")
		}
		if err := execution.ChildWorkflow.Solution.validate(); err != nil {
			return fmt.Errorf("child workflow solution: %w", err)
		}
	default:
		return fmt.Errorf("execution kind must be executor or child-workflow")
	}
	return nil
}

func (reference ContentReference) validate() error {
	if (reference.Local == nil) == (reference.External == nil) {
		return fmt.Errorf("content reference must select exactly one source")
	}
	if reference.Local != nil {
		return reference.Local.validate()
	}
	return reference.External.validate()
}

// UnmarshalJSON rejects fields that could reintroduce a second local typed-content source.
func (reference *ContentReference) UnmarshalJSON(data []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	for field := range fields {
		if field != "local" && field != "external" {
			return fmt.Errorf("content reference has unknown field %q", field)
		}
	}
	local, hasLocal := fields["local"]
	external, hasExternal := fields["external"]
	if hasLocal == hasExternal {
		return fmt.Errorf("content reference must select exactly one source")
	}
	*reference = ContentReference{}
	if hasLocal {
		value, err := unmarshalLocalContentReference(local)
		if err != nil {
			return err
		}
		reference.Local = &value
		return nil
	}
	var value ExactPackageRef
	if err := json.Unmarshal(external, &value); err != nil {
		return err
	}
	reference.External = &value
	return nil
}

func unmarshalLocalContentReference(data []byte) (LocalContentReference, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return LocalContentReference{}, err
	}
	for field := range fields {
		if field != "contentId" && field != "contentDigest" && field != "packagePath" {
			return LocalContentReference{}, fmt.Errorf("local content reference has unknown field %q", field)
		}
	}
	var reference LocalContentReference
	if err := json.Unmarshal(data, &reference); err != nil {
		return LocalContentReference{}, err
	}
	return reference, nil
}

func (reference ContentReference) validateLocal(contents map[string]PackageContent, kind ContentKind) error {
	if err := reference.validate(); err != nil {
		return err
	}
	if reference.External != nil {
		return nil
	}
	if contents == nil {
		return nil
	}
	declared, exists := contents[reference.Local.ContentID]
	if !exists || declared.Kind != kind || declared.ContentDigest != reference.Local.ContentDigest || declared.PackagePath != reference.Local.PackagePath {
		return fmt.Errorf("local %s reference is not declared with matching kind, digest, and path", kind)
	}
	return nil
}

func (content PackageContent) validate() error {
	if err := content.validateIdentity(); err != nil {
		return err
	}
	return nil
}

func (content PackageContent) validateIdentity() error {
	if !isContentKind(content.Kind) || isBlank(content.ContentID) || !isDigest(content.ContentDigest) || isBlank(content.PackagePath) {
		return fmt.Errorf("local content requires kind, id, digest, and package path")
	}
	return nil
}

func (reference LocalContentReference) validate() error {
	if isBlank(reference.ContentID) || !isDigest(reference.ContentDigest) || isBlank(reference.PackagePath) {
		return fmt.Errorf("local content reference requires id, digest, and package path")
	}
	return nil
}

func (reference ExactPackageRef) validate() error {
	if isBlank(reference.ComponentID) || !isVersion(reference.Version) || !isDigest(reference.ArtifactDigest) {
		return fmt.Errorf("exact package reference requires component id, exact version, and digest")
	}
	return nil
}

func contentIndex(contents []PackageContent) (map[string]PackageContent, error) {
	index := make(map[string]PackageContent, len(contents))
	for _, content := range contents {
		if err := content.validate(); err != nil {
			return nil, fmt.Errorf("package content: %w", err)
		}
		if _, exists := index[content.ContentID]; exists {
			return nil, fmt.Errorf("duplicate package content %q", content.ContentID)
		}
		index[content.ContentID] = content
	}
	return index, nil
}

func validateContents(contents map[string]PackageContent) error {
	for _, content := range contents {
		switch content.Kind {
		case ContentKindWorkUnitContract:
			if content.WorkUnitContract == nil || content.HarnessDefinition != nil {
				return fmt.Errorf("work unit contract content %q requires only typed contract content", content.ContentID)
			}
			if err := content.WorkUnitContract.validate(contents); err != nil {
				return fmt.Errorf("work unit contract %q: %w", content.ContentID, err)
			}
		case ContentKindHarnessDefinition:
			if content.HarnessDefinition == nil || content.WorkUnitContract != nil {
				return fmt.Errorf("harness content %q requires only typed harness content", content.ContentID)
			}
			if err := content.HarnessDefinition.validate(); err != nil {
				return fmt.Errorf("harness %q: %w", content.ContentID, err)
			}
		case ContentKindDataContract, ContentKindVerificationFixture:
			if content.WorkUnitContract != nil || content.HarnessDefinition != nil {
				return fmt.Errorf("content %q cannot carry unrelated typed content", content.ContentID)
			}
		}
	}
	return nil
}

// ValidatePackageContent validates one present package-local tagged content
// value without requiring a complete SolutionPackage.
func ValidatePackageContent(content PackageContent) error {
	contents, err := contentIndex([]PackageContent{content})
	if err != nil {
		return err
	}
	return validateContents(contents)
}

func (contract WorkUnitContract) validate(contents map[string]PackageContent) error {
	if err := contract.Display.validate("work unit contract display"); err != nil {
		return err
	}
	if len(contract.OutputSlots) == 0 {
		return fmt.Errorf("work unit contract requires output slots")
	}
	hasRequiredOutput := false
	for _, slot := range contract.OutputSlots {
		if slot.Required {
			hasRequiredOutput = true
			break
		}
	}
	if !hasRequiredOutput {
		return fmt.Errorf("work unit contract requires at least one required output slot")
	}
	keys := make(map[string]struct{}, len(contract.InputSlots)+len(contract.OutputSlots))
	for _, slot := range append(append([]ContractSlot{}, contract.InputSlots...), contract.OutputSlots...) {
		if isBlank(slot.SlotKey) {
			return fmt.Errorf("contract has blank slot key")
		}
		if _, exists := keys[slot.SlotKey]; exists {
			return fmt.Errorf("contract has duplicate slot key %q", slot.SlotKey)
		}
		keys[slot.SlotKey] = struct{}{}
		if err := slot.Display.validate("contract slot " + slot.SlotKey); err != nil {
			return err
		}
		if err := slot.DataContract.validateLocal(contents, ContentKindDataContract); err != nil {
			return fmt.Errorf("contract slot %q data contract: %w", slot.SlotKey, err)
		}
	}
	return nil
}

func (harness HarnessDefinition) validate() error {
	if err := validateOutcomeRequirements(harness.GateRequirements, "gate"); err != nil {
		return err
	}
	return validateOutcomeRequirements(harness.DecisionRequirements, "decision")
}

func validateOutcomeRequirements(requirements []OutcomeRequirement, kind string) error {
	identifiers := make(map[string]struct{}, len(requirements))
	for _, requirement := range requirements {
		if isBlank(requirement.RequirementID) || len(requirement.Outcomes) == 0 {
			return fmt.Errorf("%s requirement requires id and outcomes", kind)
		}
		if _, exists := identifiers[requirement.RequirementID]; exists {
			return fmt.Errorf("duplicate %s requirement %q", kind, requirement.RequirementID)
		}
		identifiers[requirement.RequirementID] = struct{}{}
		outcomes := make(map[string]struct{}, len(requirement.Outcomes))
		for _, outcome := range requirement.Outcomes {
			if isBlank(outcome.Value) || !isOutcomeDisposition(outcome.Disposition) {
				return fmt.Errorf("%s requirement %q has invalid outcome", kind, requirement.RequirementID)
			}
			if _, exists := outcomes[outcome.Value]; exists {
				return fmt.Errorf("%s requirement %q has duplicate outcome %q", kind, requirement.RequirementID, outcome.Value)
			}
			outcomes[outcome.Value] = struct{}{}
		}
	}
	return nil
}

func (requirement OutcomeRequirement) hasOutcome(value string) bool {
	for _, outcome := range requirement.Outcomes {
		if outcome.Value == value {
			return true
		}
	}
	return false
}

type activationKey struct {
	FromNodeID    string
	RequirementID string
	Kind          RequirementKind
}

func (activation Activation) validate(nodeID string, nodes map[string]PlanNodeTemplate, dependencies map[string][]string, contents map[string]PackageContent) error {
	if len(activation.Requirements) == 0 {
		return fmt.Errorf("activation requires typed conditions")
	}
	seen := make(map[activationKey]string, len(activation.Requirements))
	for _, condition := range activation.Requirements {
		if isBlank(condition.FromNodeID) || !isUpstream(condition.FromNodeID, nodeID, dependencies) {
			return fmt.Errorf("activation must reference an upstream node")
		}
		source, exists := nodes[condition.FromNodeID]
		if !exists {
			return fmt.Errorf("activation references unknown source node")
		}
		harness, err := resolveHarnessDefinition(source.Harness, contents)
		if err != nil {
			return err
		}
		requirement, exists := harness.requirement(condition.Kind, condition.RequirementID)
		if !exists || !requirement.hasOutcome(condition.Outcome) {
			return fmt.Errorf("activation references an undeclared requirement outcome")
		}
		key := activationKey{FromNodeID: condition.FromNodeID, RequirementID: condition.RequirementID, Kind: condition.Kind}
		if prior, exists := seen[key]; exists {
			if prior == condition.Outcome {
				return fmt.Errorf("activation duplicates a requirement condition")
			}
			return fmt.Errorf("activation contains contradictory requirement outcomes")
		}
		seen[key] = condition.Outcome
	}
	return nil
}

func (harness HarnessDefinition) requirement(kind RequirementKind, identifier string) (OutcomeRequirement, bool) {
	var requirements []OutcomeRequirement
	switch kind {
	case RequirementKindGate:
		requirements = harness.GateRequirements
	case RequirementKindDecision:
		requirements = harness.DecisionRequirements
	default:
		return OutcomeRequirement{}, false
	}
	for _, requirement := range requirements {
		if requirement.RequirementID == identifier {
			return requirement, true
		}
	}
	return OutcomeRequirement{}, false
}

type sourceOutcomeRequirement struct {
	key      activationKey
	outcomes []OutcomeDefinition
}

type outcomeAssignment map[activationKey]OutcomeDefinition

type nodeResolution uint8

const (
	nodeResolutionSelected nodeResolution = iota
	nodeResolutionObsolete
	nodeResolutionUnresolved
)

type nodeOutcomeDisposition struct {
	hasContinuation       bool
	hasTerminalSuccess    bool
	hasTerminalNonSuccess bool
}

const maxOutcomeAssignments = 1024

func validateBranchClosure(nodes map[string]PlanNodeTemplate, dependencies map[string][]string, contents map[string]PackageContent) error {
	successors := make(map[string][]string, len(nodes))
	for target, sources := range dependencies {
		for _, source := range sources {
			successors[source] = append(successors[source], target)
		}
	}
	harnesses := make(map[string]*HarnessDefinition, len(nodes))
	for nodeID, node := range nodes {
		harness, err := resolveHarnessDefinition(node.Harness, contents)
		if err != nil {
			return err
		}
		harnesses[nodeID] = harness
	}
	for nodeID, harness := range harnesses {
		requirements := outcomeRequirements(nodeID, harness)
		for _, requirement := range requirements {
			for _, outcome := range requirement.outcomes {
				if outcome.Disposition == OutcomeDispositionContinuation {
					continue
				}
				for _, successorID := range successors[nodeID] {
					if !activationExcludesOutcome(nodes[successorID], requirement.key, outcome.Value) {
						return fmt.Errorf("terminal branch %q outcome %q does not exclude successor %q", requirement.key.RequirementID, outcome.Value, successorID)
					}
				}
			}
		}
	}
	return forEachCausalOutcomeAssignment(nodes, dependencies, harnesses, func(assignment outcomeAssignment, resolutions map[string]nodeResolution) error {
		var hasTerminalSuccess, hasTerminalNonSuccess bool
		for nodeID, resolution := range resolutions {
			if resolution == nodeResolutionUnresolved {
				return fmt.Errorf("activation for node %q remains unresolved at graph quiescence", nodeID)
			}
		}
		for nodeID, harness := range harnesses {
			if resolutions[nodeID] != nodeResolutionSelected {
				continue
			}
			disposition := assignment.dispositionFor(nodeID, harness)
			hasTerminalSuccess = hasTerminalSuccess || disposition.hasTerminalSuccess
			hasTerminalNonSuccess = hasTerminalNonSuccess || disposition.hasTerminalNonSuccess
			for _, requirement := range outcomeRequirements(nodeID, harness) {
				outcome := assignment[requirement.key]
				if outcome.Disposition != OutcomeDispositionContinuation {
					continue
				}
				if !hasConvergentSuccessor(requirement.key, outcome.Value, nodes, successors, assignment, resolutions, harnesses) {
					return fmt.Errorf("continuation outcome %q on node %q has no convergent successor path", requirement.key.RequirementID, nodeID)
				}
			}
		}
		if hasTerminalSuccess && hasTerminalNonSuccess {
			return fmt.Errorf("activation assignment contains both terminal success and terminal non-success outcomes")
		}
		return nil
	})
}

func outcomeRequirements(nodeID string, harness *HarnessDefinition) []sourceOutcomeRequirement {
	requirements := make([]sourceOutcomeRequirement, 0, len(harness.GateRequirements)+len(harness.DecisionRequirements))
	for _, item := range []struct {
		kind         RequirementKind
		requirements []OutcomeRequirement
	}{{RequirementKindGate, harness.GateRequirements}, {RequirementKindDecision, harness.DecisionRequirements}} {
		for _, requirement := range item.requirements {
			requirements = append(requirements, sourceOutcomeRequirement{
				key:      activationKey{FromNodeID: nodeID, RequirementID: requirement.RequirementID, Kind: item.kind},
				outcomes: requirement.Outcomes,
			})
		}
	}
	return requirements
}

func forEachOutcomeAssignment(requirements []sourceOutcomeRequirement, visit func(outcomeAssignment) error) error {
	assignments := 0
	current := make(outcomeAssignment, len(requirements))
	var walk func(int) error
	walk = func(index int) error {
		if index == len(requirements) {
			assignments++
			if assignments > maxOutcomeAssignments {
				return fmt.Errorf("activation outcome combinations exceed limit %d", maxOutcomeAssignments)
			}
			return visit(current)
		}
		for _, outcome := range requirements[index].outcomes {
			current[requirements[index].key] = outcome
			if err := walk(index + 1); err != nil {
				return err
			}
		}
		delete(current, requirements[index].key)
		return nil
	}
	return walk(0)
}

func forEachCausalOutcomeAssignment(nodes map[string]PlanNodeTemplate, dependencies map[string][]string, harnesses map[string]*HarnessDefinition, visit func(outcomeAssignment, map[string]nodeResolution) error) error {
	order := topologicalNodeOrder(nodes, dependencies)
	assignment := make(outcomeAssignment)
	resolutions := make(map[string]nodeResolution, len(nodes))
	assignments := 0
	var walkNodes func(int) error
	walkNodes = func(index int) error {
		if index == len(order) {
			assignments++
			if assignments > maxOutcomeAssignments {
				return fmt.Errorf("activation outcome combinations exceed limit %d", maxOutcomeAssignments)
			}
			return visit(assignment, resolutions)
		}

		nodeID := order[index]
		node := nodes[nodeID]
		resolution := resolveNode(node, dependencies[nodeID], assignment, resolutions)
		resolutions[nodeID] = resolution
		defer delete(resolutions, nodeID)
		if resolution != nodeResolutionSelected {
			return walkNodes(index + 1)
		}
		requirements := outcomeRequirements(nodeID, harnesses[nodeID])
		var walkRequirements func(int) error
		walkRequirements = func(requirementIndex int) error {
			if requirementIndex == len(requirements) {
				return walkNodes(index + 1)
			}
			requirement := requirements[requirementIndex]
			for _, outcome := range requirement.outcomes {
				assignment[requirement.key] = outcome
				if err := walkRequirements(requirementIndex + 1); err != nil {
					return err
				}
			}
			delete(assignment, requirement.key)
			return nil
		}
		return walkRequirements(0)
	}
	return walkNodes(0)
}

func topologicalNodeOrder(nodes map[string]PlanNodeTemplate, dependencies map[string][]string) []string {
	successors := make(map[string][]string, len(nodes))
	pending := make(map[string]int, len(nodes))
	ready := make([]string, 0, len(nodes))
	for nodeID := range nodes {
		pending[nodeID] = len(dependencies[nodeID])
		if pending[nodeID] == 0 {
			ready = append(ready, nodeID)
		}
		for _, predecessor := range dependencies[nodeID] {
			successors[predecessor] = append(successors[predecessor], nodeID)
		}
	}
	sort.Strings(ready)
	order := make([]string, 0, len(nodes))
	for len(ready) > 0 {
		nodeID := ready[0]
		ready = ready[1:]
		order = append(order, nodeID)
		for _, successor := range successors[nodeID] {
			pending[successor]--
			if pending[successor] == 0 {
				ready = append(ready, successor)
			}
		}
		sort.Strings(ready)
	}
	return order
}

func resolveNode(node PlanNodeTemplate, predecessors []string, assignment outcomeAssignment, resolutions map[string]nodeResolution) nodeResolution {
	activationResolution := resolveActivation(node, assignment, resolutions)
	if activationResolution != nodeResolutionSelected {
		return activationResolution
	}
	for _, predecessor := range predecessors {
		switch resolutions[predecessor] {
		case nodeResolutionSelected:
		case nodeResolutionObsolete:
			return nodeResolutionObsolete
		default:
			return nodeResolutionUnresolved
		}
	}
	return nodeResolutionSelected
}

func resolveActivation(node PlanNodeTemplate, assignment outcomeAssignment, resolutions map[string]nodeResolution) nodeResolution {
	if node.Activation == nil {
		return nodeResolutionSelected
	}
	unresolved := false
	for _, condition := range node.Activation.Requirements {
		if resolutions[condition.FromNodeID] != nodeResolutionSelected {
			unresolved = true
			continue
		}
		outcome, exists := assignment[activationKeyFor(condition)]
		if !exists {
			unresolved = true
			continue
		}
		if outcome.Value != condition.Outcome {
			return nodeResolutionObsolete
		}
	}
	if unresolved {
		return nodeResolutionUnresolved
	}
	return nodeResolutionSelected
}

func (assignment outcomeAssignment) dispositionFor(nodeID string, harness *HarnessDefinition) nodeOutcomeDisposition {
	var disposition nodeOutcomeDisposition
	for _, requirement := range outcomeRequirements(nodeID, harness) {
		outcome, exists := assignment[requirement.key]
		if !exists {
			continue
		}
		switch outcome.Disposition {
		case OutcomeDispositionContinuation:
			disposition.hasContinuation = true
		case OutcomeDispositionTerminalSuccess:
			disposition.hasTerminalSuccess = true
		case OutcomeDispositionTerminalNonSuccess:
			disposition.hasTerminalNonSuccess = true
		}
	}
	return disposition
}

func activationExcludesOutcome(node PlanNodeTemplate, key activationKey, outcome string) bool {
	if node.Activation == nil {
		return false
	}
	for _, condition := range node.Activation.Requirements {
		if activationKeyFor(condition) == key && condition.Outcome != outcome {
			return true
		}
	}
	return false
}

func hasConvergentSuccessor(requirement activationKey, outcome string, nodes map[string]PlanNodeTemplate, successors map[string][]string, assignment outcomeAssignment, resolutions map[string]nodeResolution, harnesses map[string]*HarnessDefinition) bool {
	for _, successorID := range successors[requirement.FromNodeID] {
		if resolutions[successorID] != nodeResolutionSelected || !activationSelectsOutcome(nodes[successorID], requirement, outcome) {
			continue
		}
		if reachesSuccessfulTerminal(successorID, successors, assignment, resolutions, harnesses, map[string]bool{}) {
			return true
		}
	}
	return false
}

func activationSelectsOutcome(node PlanNodeTemplate, requirement activationKey, outcome string) bool {
	if node.Activation == nil {
		return false
	}
	for _, condition := range node.Activation.Requirements {
		if activationKeyFor(condition) == requirement && condition.Outcome == outcome {
			return true
		}
	}
	return false
}

func reachesSuccessfulTerminal(nodeID string, successors map[string][]string, assignment outcomeAssignment, resolutions map[string]nodeResolution, harnesses map[string]*HarnessDefinition, seen map[string]bool) bool {
	if seen[nodeID] {
		return false
	}
	disposition := assignment.dispositionFor(nodeID, harnesses[nodeID])
	if disposition.hasTerminalNonSuccess {
		return false
	}
	if disposition.hasTerminalSuccess {
		return true
	}
	seen[nodeID] = true
	defer delete(seen, nodeID)
	hasSelectedSuccessor := false
	for _, successor := range successors[nodeID] {
		if resolutions[successor] != nodeResolutionSelected {
			continue
		}
		hasSelectedSuccessor = true
		if reachesSuccessfulTerminal(successor, successors, assignment, resolutions, harnesses, seen) {
			return true
		}
	}
	return !hasSelectedSuccessor && !disposition.hasContinuation
}

func activationMatchesAssignment(node PlanNodeTemplate, assignment outcomeAssignment) bool {
	if node.Activation == nil {
		return true
	}
	for _, condition := range node.Activation.Requirements {
		outcome, exists := assignment[activationKeyFor(condition)]
		if !exists || outcome.Value != condition.Outcome {
			return false
		}
	}
	return true
}

func activationKeyFor(condition ActivationRequirement) activationKey {
	return activationKey{FromNodeID: condition.FromNodeID, RequirementID: condition.RequirementID, Kind: condition.Kind}
}

func resolveWorkUnitContract(reference ContentReference, contents map[string]PackageContent) (*WorkUnitContract, error) {
	if err := reference.validateLocal(contents, ContentKindWorkUnitContract); err != nil {
		return nil, err
	}
	if reference.Local != nil {
		contract := contents[reference.Local.ContentID].WorkUnitContract
		if contract == nil {
			return nil, fmt.Errorf("local work unit contract has no typed definition")
		}
		return contract, nil
	}
	if reference.Resolved == nil || reference.Resolved.WorkUnitContract == nil {
		return nil, fmt.Errorf("external work unit contract must be resolved before typed binding validation")
	}
	if err := reference.Resolved.WorkUnitContract.validate(contents); err != nil {
		return nil, err
	}
	return reference.Resolved.WorkUnitContract, nil
}

func resolveHarnessDefinition(reference ContentReference, contents map[string]PackageContent) (*HarnessDefinition, error) {
	if err := reference.validateLocal(contents, ContentKindHarnessDefinition); err != nil {
		return nil, err
	}
	if reference.Local != nil {
		harness := contents[reference.Local.ContentID].HarnessDefinition
		if harness == nil {
			return nil, fmt.Errorf("local harness has no typed definition")
		}
		return harness, nil
	}
	if reference.Resolved == nil || reference.Resolved.HarnessDefinition == nil {
		return nil, fmt.Errorf("external harness must be resolved before activation validation")
	}
	if err := reference.Resolved.HarnessDefinition.validate(); err != nil {
		return nil, err
	}
	return reference.Resolved.HarnessDefinition, nil
}

func slotIndex(slots []ResponsibilitySlot) map[string]ResponsibilitySlot {
	index := make(map[string]ResponsibilitySlot, len(slots))
	for _, slot := range slots {
		index[slot.SlotKey] = slot
	}
	return index
}

func includesAll(actual, required []string) bool {
	for _, capability := range required {
		if !contains(actual, capability) {
			return false
		}
	}
	return true
}

func validateBinding(target, source PlanNodeTemplate, binding InputBinding, contents map[string]PackageContent) error {
	sourceContract, err := resolveWorkUnitContract(source.WorkUnitContract, contents)
	if err != nil {
		return err
	}
	targetContract, err := resolveWorkUnitContract(target.WorkUnitContract, contents)
	if err != nil {
		return err
	}
	sourceSlot, foundSource := findSlot(sourceContract.OutputSlots, binding.FromOutputSlot)
	targetSlot, foundTarget := findSlot(targetContract.InputSlots, binding.ToInputSlot)
	if !foundSource || !foundTarget || !sameReference(sourceSlot.DataContract, targetSlot.DataContract) {
		return fmt.Errorf("input binding does not connect compatible typed contract slots")
	}
	return nil
}

func findSlot(slots []ContractSlot, key string) (ContractSlot, bool) {
	for _, slot := range slots {
		if slot.SlotKey == key {
			return slot, true
		}
	}
	return ContractSlot{}, false
}

func sameReference(left, right ContentReference) bool {
	if left.Local != nil && right.Local != nil {
		return left.Local.ContentID == right.Local.ContentID && left.Local.ContentDigest == right.Local.ContentDigest && left.Local.PackagePath == right.Local.PackagePath
	}
	if left.External != nil && right.External != nil {
		return *left.External == *right.External
	}
	return false
}

func (experience DeclarativeExperience) validate() error {
	for term, display := range experience.Terminology {
		if isBlank(term) {
			return fmt.Errorf("terminology has blank key")
		}
		if err := display.validate("terminology " + term); err != nil {
			return err
		}
	}
	return validateExperienceElements(experience.Views, experience.Forms, experience.Layouts, experience.Renderers)
}

func validateExperienceElements(groups ...any) error {
	for _, group := range groups {
		switch elements := group.(type) {
		case []ExperienceView:
			for _, element := range elements {
				if err := validateExperienceElement(element.ID, element.Display); err != nil {
					return err
				}
			}
		case []ExperienceForm:
			for _, element := range elements {
				if err := validateExperienceElement(element.ID, element.Display); err != nil {
					return err
				}
			}
		case []ExperienceLayout:
			for _, element := range elements {
				if err := validateExperienceElement(element.ID, element.Display); err != nil {
					return err
				}
			}
		case []ExperienceRenderer:
			for _, element := range elements {
				if err := validateExperienceElement(element.ID, element.Display); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateExperienceElement(identifier string, display Display) error {
	if isBlank(identifier) {
		return fmt.Errorf("experience element requires id")
	}
	return display.validate("experience element " + identifier)
}

func isFieldType(fieldType FieldType) bool {
	switch fieldType {
	case FieldTypeText, FieldTypeNumber, FieldTypeBoolean, FieldTypeChoice, FieldTypeRepositoryBinding, FieldTypeCredentialBinding, FieldTypeDeploymentTargetBinding, FieldTypeArtifactInput:
		return true
	default:
		return false
	}
}

func isContentKind(kind ContentKind) bool {
	switch kind {
	case ContentKindDataContract, ContentKindWorkUnitContract, ContentKindHarnessDefinition, ContentKindVerificationFixture:
		return true
	default:
		return false
	}
}

func isVersion(version string) bool {
	return version != "latest" && semver.IsValid("v"+version)
}

func isDigest(digest string) bool {
	if !strings.HasPrefix(digest, "sha256:") || len(digest) != len("sha256:")+64 {
		return false
	}
	for _, character := range digest[len("sha256:"):] {
		if !(character >= '0' && character <= '9') && !(character >= 'a' && character <= 'f') {
			return false
		}
	}
	return true
}

func isUpstream(source, target string, dependencies map[string][]string) bool {
	for _, dependency := range dependencies[target] {
		if dependency == source || isUpstream(source, dependency, dependencies) {
			return true
		}
	}
	return false
}

func hasCycle(dependencies map[string][]string) bool {
	visiting := make(map[string]bool, len(dependencies))
	visited := make(map[string]bool, len(dependencies))
	var visit func(string) bool
	visit = func(node string) bool {
		if visiting[node] {
			return true
		}
		if visited[node] {
			return false
		}
		visiting[node] = true
		for _, dependency := range dependencies[node] {
			if visit(dependency) {
				return true
			}
		}
		visiting[node] = false
		visited[node] = true
		return false
	}
	for node := range dependencies {
		if visit(node) {
			return true
		}
	}
	return false
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func isOutcomeDisposition(disposition OutcomeDisposition) bool {
	switch disposition {
	case OutcomeDispositionContinuation, OutcomeDispositionTerminalSuccess, OutcomeDispositionTerminalNonSuccess:
		return true
	default:
		return false
	}
}

func hasBlank(values []string) bool {
	for _, value := range values {
		if isBlank(value) {
			return true
		}
	}
	return false
}

func isBlank(value string) bool {
	return strings.TrimSpace(value) == ""
}

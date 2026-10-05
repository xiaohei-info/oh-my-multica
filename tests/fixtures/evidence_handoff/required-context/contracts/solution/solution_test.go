package solution

import "testing"

func TestSolutionPackageValidateAcceptsPinnedBaseline(t *testing.T) {
	pkg := validSolutionPackage()

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestSolutionPackageValidateRejectsUnpinnedDependency(t *testing.T) {
	pkg := validSolutionPackage()
	pkg.PackageDependencies[0].Version = "latest"

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want pinned dependency error")
	}
}

func TestBaselinePlanTemplateValidateRejectsInputOutsideDependencyClosure(t *testing.T) {
	pkg := validSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes[1].InputBindings = []InputBinding{{
		FromNodeID:     "unrelated",
		FromOutputSlot: "output",
		ToInputSlot:    "input",
	}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want input binding dependency error")
	}
}

func TestBaselinePlanTemplateHasExplicitDAGOrchestrationNode(t *testing.T) {
	pkg := validSolutionPackage()

	if !pkg.BaselinePlanTemplate.HasDAGOrchestrationNode() {
		t.Fatal("HasDAGOrchestrationNode() = false, want true")
	}
}

func TestSolutionPackageValidationRejectsInvalidPublishedValues(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*SolutionPackage)
	}{
		{"blank display", func(pkg *SolutionPackage) { pkg.Display.Name = nil }},
		{"unsupported form field", func(pkg *SolutionPackage) { pkg.ProjectSetupSchema.Fields[0].FieldType = "script" }},
		{"choice without options", func(pkg *SolutionPackage) { pkg.ProjectSetupSchema.Fields[0].FieldType = FieldTypeChoice }},
		{"options on text", func(pkg *SolutionPackage) {
			pkg.WorkflowInputSchema.Fields[0].Options = []FormOption{{Value: "x", Display: display("X")}}
		}},
		{"unsupported slot", func(pkg *SolutionPackage) { pkg.ResponsibilitySlots[0].SetupRequirement = "optional" }},
		{"unqualified local fixture", func(pkg *SolutionPackage) { pkg.VerificationFixtures[0].Local.ContentDigest = "sha256:short" }},
		{"external fixture", func(pkg *SolutionPackage) {
			pkg.VerificationFixtures[0] = ContentReference{External: &ExactPackageRef{ComponentID: "fixture", Version: "1.0.0", ArtifactDigest: testDigest('f')}}
		}},
		{"invalid plan edge", func(pkg *SolutionPackage) { pkg.BaselinePlanTemplate.Edges[0].ToNodeID = "missing" }},
		{"cycle", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Edges = append(pkg.BaselinePlanTemplate.Edges, PlanEdge{FromNodeID: "delivery-dag", ToNodeID: "solution-design"})
		}},
		{"unconfigured executor", func(pkg *SolutionPackage) { pkg.BaselinePlanTemplate.Nodes[0].Execution.Executor = nil }},
		{"missing contract binding", func(pkg *SolutionPackage) { pkg.BaselinePlanTemplate.Nodes[0].WorkUnitContract = ContentReference{} }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			pkg := validSolutionPackage()
			test.mutate(&pkg)
			if err := pkg.Validate(); err == nil {
				t.Fatal("Validate() error = nil")
			}
		})
	}
}

func TestExecutionValidationSupportsChildWorkflowOnly(t *testing.T) {
	execution := Execution{
		Kind: ExecutionKindChildWorkflow,
		ChildWorkflow: &ChildWorkflowExecution{Solution: ExactPackageRef{
			ComponentID: "child-solution", Version: "1.0.0", ArtifactDigest: testDigest('a'),
		}},
	}
	if err := execution.validate(); err != nil {
		t.Fatalf("validate() error = %v", err)
	}

	execution.Executor = &ExecutorExecution{CapabilityRequirements: []string{"extra"}}
	if err := execution.validate(); err == nil {
		t.Fatal("validate() error = nil")
	}
}

func TestContractHelpersRejectUnsafeValues(t *testing.T) {
	for _, fieldType := range []FieldType{FieldTypeText, FieldTypeNumber, FieldTypeBoolean, FieldTypeChoice, FieldTypeRepositoryBinding, FieldTypeCredentialBinding, FieldTypeDeploymentTargetBinding, FieldTypeArtifactInput} {
		if !isFieldType(fieldType) {
			t.Fatalf("isFieldType(%q) = false", fieldType)
		}
	}
	if isFieldType("script") {
		t.Fatal("isFieldType(script) = true")
	}
	if !isDigest(testDigest('a')) || isDigest("sha256:"+string(makeRunes('A', 64))) || isDigest("sha512:abc") {
		t.Fatal("isDigest() accepted or rejected an unexpected digest")
	}
	if !hasCycle(map[string][]string{"a": {"b"}, "b": {"a"}}) || hasCycle(map[string][]string{"a": {"b"}}) {
		t.Fatal("hasCycle() returned an unexpected result")
	}
}

func TestSchemaAndSlotValidatorsRejectInvalidDetails(t *testing.T) {
	if err := (Display{Name: LocalizedText{"": "Name"}, Description: LocalizedText{"en-US": "Description"}}).validate("display"); err == nil {
		t.Fatal("display validation error = nil")
	}
	if err := (Display{Name: LocalizedText{"en-US": "Name"}, Description: LocalizedText{"": "Description"}}).validate("display"); err == nil {
		t.Fatal("display validation error = nil")
	}

	choice := FormField{FieldKey: "choice", Display: display("Choice"), FieldType: FieldTypeChoice, Options: []FormOption{{Value: "yes", Display: display("Yes")}}}
	if err := (FormSchema{Fields: []FormField{choice}}).validate("schema"); err != nil {
		t.Fatalf("choice schema validation error = %v", err)
	}
	choice.Options[0].Display.Name = nil
	if err := (FormSchema{Fields: []FormField{choice}}).validate("schema"); err == nil {
		t.Fatal("choice option display validation error = nil")
	}
	duplicate := FormField{FieldKey: "choice", Display: display("Second"), FieldType: FieldTypeText}
	if err := (FormSchema{Fields: []FormField{duplicate, duplicate}}).validate("schema"); err == nil {
		t.Fatal("duplicate field validation error = nil")
	}

	slot := validSolutionPackage().ResponsibilitySlots[0]
	if err := validateSlots([]ResponsibilitySlot{slot, slot}); err == nil {
		t.Fatal("duplicate slot validation error = nil")
	}
	slot = validSolutionPackage().ResponsibilitySlots[0]
	slot.CapabilityRequirements = []string{""}
	if err := validateSlots([]ResponsibilitySlot{slot}); err == nil {
		t.Fatal("blank capability validation error = nil")
	}
	slot = validSolutionPackage().ResponsibilitySlots[0]
	slot.RecommendedAgentTemplates[0].ArtifactDigest = "bad"
	if err := validateSlots([]ResponsibilitySlot{slot}); err == nil {
		t.Fatal("invalid template validation error = nil")
	}
}

func TestNodeAndReferenceValidatorsRejectInvalidDetails(t *testing.T) {
	pkg := validSolutionPackage()
	node := pkg.BaselinePlanTemplate.Nodes[0]
	node.Display.Name = nil
	if err := node.validate(); err == nil {
		t.Fatal("node display validation error = nil")
	}
	node = pkg.BaselinePlanTemplate.Nodes[0]
	node.ResponsibilityRequirement.CapabilityRequirements = nil
	if err := node.validate(); err == nil {
		t.Fatal("node responsibility validation error = nil")
	}
	node = pkg.BaselinePlanTemplate.Nodes[0]
	node.Harness = ContentReference{}
	if err := node.validate(); err == nil {
		t.Fatal("node harness validation error = nil")
	}

	local := localContent("local", "guide")
	local.External = &ExactPackageRef{ComponentID: "external", Version: "1.0.0", ArtifactDigest: testDigest('a')}
	if err := local.validate(); err == nil {
		t.Fatal("ambiguous reference validation error = nil")
	}
	if err := (PackageContent{}).validate(); err == nil {
		t.Fatal("empty package content validation error = nil")
	}

	template := pkg.BaselinePlanTemplate
	template.Nodes = append(template.Nodes, template.Nodes[0])
	if err := template.validate(); err == nil {
		t.Fatal("duplicate node validation error = nil")
	}
	if pkg.BaselinePlanTemplate.HasDAGOrchestrationNode() != true {
		t.Fatal("expected explicit DAG orchestration node")
	}
	pkg.BaselinePlanTemplate.Nodes[1].Execution.Executor.CapabilityRequirements = nil
	if pkg.BaselinePlanTemplate.HasDAGOrchestrationNode() {
		t.Fatal("expected no explicit DAG orchestration node")
	}
}

func TestExecutionRejectsUnknownAndInvalidChildSolution(t *testing.T) {
	if err := (Execution{Kind: "unknown"}).validate(); err == nil {
		t.Fatal("unknown execution validation error = nil")
	}
	if err := (Execution{Kind: ExecutionKindChildWorkflow, ChildWorkflow: &ChildWorkflowExecution{Solution: ExactPackageRef{}}}).validate(); err == nil {
		t.Fatal("invalid child workflow solution validation error = nil")
	}
}

func baseSolutionPackage() SolutionPackage {
	contract := localContent("solution-design-contract", "contract")
	harness := localContent("solution-design-harness", "harness")
	return SolutionPackage{
		SolutionID:     "software-delivery",
		Version:        "1.0.0",
		ArtifactDigest: testDigest('a'),
		Display:        display("Software Delivery"),
		ProjectSetupSchema: FormSchema{Fields: []FormField{{
			FieldKey:  "repository",
			Display:   display("Repository"),
			FieldType: FieldTypeRepositoryBinding,
			Required:  true,
		}}},
		ResponsibilitySlots: []ResponsibilitySlot{{
			SlotKey:                "architect",
			Display:                display("Architect"),
			SetupRequirement:       SetupRequirementRequired,
			CapabilityRequirements: []string{"architecture-design"},
			RecommendedAgentTemplates: []ExactPackageRef{{
				ComponentID: "software-architect", Version: "1.0.0", ArtifactDigest: testDigest('b'),
			}},
		}},
		WorkflowInputSchema: FormSchema{Fields: []FormField{{
			FieldKey: "delivery-context", Display: display("Delivery Context"), FieldType: FieldTypeText,
		}}},
		BaselinePlanTemplate: BaselinePlanTemplate{
			TemplateID: "baseline-software-delivery", ContentDigest: testDigest('c'), Display: display("Baseline"),
			Nodes: []PlanNodeTemplate{
				{
					NodeID: "solution-design", Display: display("Solution Design"), WorkUnitContract: contract,
					ResponsibilityRequirement: ResponsibilityRequirement{SlotKey: "architect", CapabilityRequirements: []string{"architecture-design"}},
					Execution:                 Execution{Kind: ExecutionKindExecutor, Executor: &ExecutorExecution{CapabilityRequirements: []string{"architecture-design"}}},
					Harness:                   harness,
				},
				{
					NodeID: "delivery-dag", Display: display("Delivery DAG"), WorkUnitContract: localContent("delivery-contract", "contract"),
					ResponsibilityRequirement: ResponsibilityRequirement{SlotKey: "architect", CapabilityRequirements: []string{"orchestration.dag"}},
					InputBindings:             []InputBinding{{FromNodeID: "solution-design", FromOutputSlot: "design", ToInputSlot: "design"}},
					Execution:                 Execution{Kind: ExecutionKindExecutor, Executor: &ExecutorExecution{CapabilityRequirements: []string{"orchestration.dag"}}},
					Harness:                   localContent("delivery-harness", "harness"),
				},
			},
			Edges: []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}},
		},
		PackageContents:      []PackageContent{{Kind: "contract", ContentID: "solution-design-contract", ContentDigest: testDigest('d'), PackagePath: "contracts/solution/solution.go"}},
		PackageDependencies:  []ExactPackageRef{{ComponentID: "harness-extension", Version: "1.0.0", ArtifactDigest: testDigest('e')}},
		VerificationFixtures: []ContentReference{localContent("fixture-golden", "fixture")},
	}
}

func validSolutionPackage() SolutionPackage {
	return completeSolutionPackage()
}

func localContent(contentID string, kind ContentKind) ContentReference {
	return ContentReference{Local: &LocalContentReference{ContentID: contentID, ContentDigest: testDigest('f'), PackagePath: "solution/" + contentID}}
}

func display(name string) Display {
	return Display{Name: LocalizedText{"en-US": name}, Description: LocalizedText{"en-US": name + " description"}}
}

func testDigest(character rune) string {
	return "sha256:" + string(makeRunes(character, 64))
}

func makeRunes(character rune, count int) []rune {
	runes := make([]rune, count)
	for index := range runes {
		runes[index] = character
	}
	return runes
}

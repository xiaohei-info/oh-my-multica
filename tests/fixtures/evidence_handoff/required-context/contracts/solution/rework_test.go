package solution

import (
	"encoding/json"
	"testing"
)

func TestSolutionPackageValidatesCanonicalContentSerialization(t *testing.T) {
	pkg := completeSolutionPackage()
	encoded, err := json.Marshal(pkg)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	var wire any
	if err := json.Unmarshal(encoded, &wire); err != nil {
		t.Fatalf("Unmarshal() wire error = %v", err)
	}
	if got := typedPayloadCount(wire, "workUnitContract"); got != 3 {
		t.Fatalf("workUnitContract wire sources = %d, want 3 canonical package contents", got)
	}
	if got := wireKeyCount(wire, "harnessDefinition"); got != 3 {
		t.Fatalf("harnessDefinition wire sources = %d, want 3 canonical package contents", got)
	}
	var decoded SolutionPackage
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("Unmarshal() error = %v", err)
	}
	if err := decoded.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestContentReferenceRejectsEmbeddedPayload(t *testing.T) {
	pkg := completeSolutionPackage()
	encoded, err := json.Marshal(pkg)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatalf("Unmarshal() document error = %v", err)
	}
	nodes := document["baselinePlanTemplate"].(map[string]any)["nodes"].([]any)
	local := nodes[0].(map[string]any)["workUnitContract"].(map[string]any)["local"].(map[string]any)
	local["workUnitContract"] = map[string]any{"display": map[string]any{}}
	encoded, err = json.Marshal(document)
	if err != nil {
		t.Fatalf("Marshal() mutated document error = %v", err)
	}
	var decoded SolutionPackage
	if err := json.Unmarshal(encoded, &decoded); err == nil {
		t.Fatal("Unmarshal() error = nil")
	}
}

func TestContentReferenceStrictJSON(t *testing.T) {
	local := LocalContentReference{ContentID: "local", ContentDigest: testDigest('a'), PackagePath: "contents/local"}
	external := ExactPackageRef{ComponentID: "external", Version: "1.0.0", ArtifactDigest: testDigest('b')}
	localJSON, err := json.Marshal(map[string]any{"local": local})
	if err != nil {
		t.Fatalf("Marshal() local error = %v", err)
	}
	externalJSON, err := json.Marshal(map[string]any{"external": external})
	if err != nil {
		t.Fatalf("Marshal() external error = %v", err)
	}

	for _, test := range []struct {
		name    string
		encoded []byte
		wantErr bool
	}{
		{"local", localJSON, false},
		{"external", externalJSON, false},
		{"both sources", mustMarshal(t, map[string]any{"local": local, "external": external}), true},
		{"unknown reference field", []byte(`{"unknown":true}`), true},
		{"malformed external", []byte(`{"external":"wrong"}`), true},
		{"malformed local", []byte(`{"local":[]}`), true},
		{"invalid JSON", []byte(`{`), true},
	} {
		t.Run(test.name, func(t *testing.T) {
			var reference ContentReference
			err := json.Unmarshal(test.encoded, &reference)
			if (err != nil) != test.wantErr {
				t.Fatalf("Unmarshal() error = %v, wantErr %t", err, test.wantErr)
			}
		})
	}
}

func TestSolutionPackageAllowsTerminalDecisionOutcomes(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{DecisionRequirements: []OutcomeRequirement{{
		RequirementID: "design-approved",
		Outcomes:      []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "approved",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = nil
	packageContent(&pkg, "change-harness").HarnessDefinition.DecisionRequirements = []OutcomeRequirement{{
		RequirementID: "terminal-decision",
		Outcomes: []OutcomeDefinition{
			{Value: "approved", Disposition: OutcomeDispositionTerminalSuccess},
			{Value: "rejected", Disposition: OutcomeDispositionTerminalNonSuccess},
		},
	}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestSolutionPackageAllowsMixedContinuationAndTerminalOutcomes(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:2]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "quality-gate",
		Outcomes: []OutcomeDefinition{
			{Value: "passed", Disposition: OutcomeDispositionContinuation},
			{Value: "failed", Disposition: OutcomeDispositionTerminalNonSuccess},
		},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "passed",
	}}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestSolutionPackageRejectsTerminalFailureActivation(t *testing.T) {
	pkg := completeSolutionPackage()
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "quality-gate",
		Outcomes: []OutcomeDefinition{
			{Value: "passed", Disposition: OutcomeDispositionContinuation},
			{Value: "failed", Disposition: OutcomeDispositionTerminalNonSuccess},
		},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "passed",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "failed",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil")
	}
}

func TestSolutionPackageRejectsTerminalSuccessWithUnconditionalSuccessor(t *testing.T) {
	pkg := completeSolutionPackage()
	packageContent(&pkg, "solution-design-harness").HarnessDefinition.DecisionRequirements = []OutcomeRequirement{{
		RequirementID: "design-approved",
		Outcomes: []OutcomeDefinition{
			{Value: "approved", Disposition: OutcomeDispositionTerminalSuccess},
			{Value: "rejected", Disposition: OutcomeDispositionContinuation},
		},
	}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = nil

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil")
	}
}

func TestSolutionPackageRejectsContinuationCrossProductHoles(t *testing.T) {
	pkg := completeSolutionPackage()
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{
		{
			RequirementID: "r1",
			Outcomes: []OutcomeDefinition{
				{Value: "a", Disposition: OutcomeDispositionContinuation},
				{Value: "b", Disposition: OutcomeDispositionContinuation},
			},
		},
		{
			RequirementID: "r2",
			Outcomes: []OutcomeDefinition{
				{Value: "x", Disposition: OutcomeDispositionContinuation},
				{Value: "y", Disposition: OutcomeDispositionContinuation},
			},
		},
	}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{
		{FromNodeID: "solution-design", RequirementID: "r1", Kind: RequirementKindGate, Outcome: "a"},
		{FromNodeID: "solution-design", RequirementID: "r2", Kind: RequirementKindGate, Outcome: "x"},
	}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{
		{FromNodeID: "solution-design", RequirementID: "r1", Kind: RequirementKindGate, Outcome: "b"},
		{FromNodeID: "solution-design", RequirementID: "r2", Kind: RequirementKindGate, Outcome: "y"},
	}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil")
	}
}

func TestSolutionPackageRejectsOutcomeFromObsoleteSource(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "solution-design", ToNodeID: "change-design"},
		{FromNodeID: "delivery-dag", ToNodeID: "change-design"},
	}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "deliver", Disposition: OutcomeDispositionContinuation},
			{Value: "change", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "ready",
		Outcomes:      []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionContinuation}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "deliver",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "delivery-dag", RequirementID: "ready", Kind: RequirementKindGate, Outcome: "yes",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want obsolete-source branch rejection")
	}
}

func TestSolutionPackageRejectsSuccessorWithObsoleteHardPredecessor(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "solution-design", ToNodeID: "change-design"},
		{FromNodeID: "delivery-dag", ToNodeID: "change-design"},
	}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "deliver", Disposition: OutcomeDispositionContinuation},
			{Value: "change", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "deliver",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "change",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want unselected hard-predecessor rejection")
	}
}

func TestSolutionPackageAllowsCausallyReachableMultiRootConjunction(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "change-design"},
		{FromNodeID: "delivery-dag", ToNodeID: "change-design"},
	}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = nil
	pkg.BaselinePlanTemplate.Nodes[1].InputBindings = nil
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "design",
		Outcomes:      []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "delivery",
		Outcomes:      []OutcomeDefinition{{Value: "ready", Disposition: OutcomeDispositionContinuation}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{
		{FromNodeID: "solution-design", RequirementID: "design", Kind: RequirementKindGate, Outcome: "approved"},
		{FromNodeID: "delivery-dag", RequirementID: "delivery", Kind: RequirementKindGate, Outcome: "ready"},
	}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestSolutionPackageCausalEnumerationDoesNotCountMutuallyExclusiveSourcesTogether(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "solution-design", ToNodeID: "change-design"},
	}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "deliver", Disposition: OutcomeDispositionContinuation},
			{Value: "change", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: terminalOutcomeRequirements("delivery", 9)}
	packageContent(&pkg, "change-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: terminalOutcomeRequirements("change", 9)}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "deliver",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "change",
	}}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v, want causal combinations within limit", err)
	}
}

func TestSolutionPackageRejectsUnsatisfiableConjunctiveSuccessor(t *testing.T) {
	pkg := completeSolutionPackage()
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "quality-gate",
		Outcomes: []OutcomeDefinition{
			{Value: "passed", Disposition: OutcomeDispositionContinuation},
			{Value: "failed", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{
		{FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "passed"},
		{FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "failed"},
	}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "failed",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil")
	}
}

func TestSolutionPackageRejectsActivationIncompatibleTerminalPath(t *testing.T) {
	pkg := completeSolutionPackage()
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "quality-gate",
		Outcomes: []OutcomeDefinition{
			{Value: "passed", Disposition: OutcomeDispositionContinuation},
			{Value: "failed", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "passed",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "quality-gate", Kind: RequirementKindGate, Outcome: "failed",
	}}}
	pkg.BaselinePlanTemplate.Edges = append(pkg.BaselinePlanTemplate.Edges, PlanEdge{FromNodeID: "delivery-dag", ToNodeID: "change-design"})

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil")
	}
}

func TestSolutionPackageRejectsContinuationEndingAtTerminalNonSuccess(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:2]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{DecisionRequirements: []OutcomeRequirement{{
		RequirementID: "design-approved",
		Outcomes:      []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "delivery-result",
		Outcomes:      []OutcomeDefinition{{Value: "failed", Disposition: OutcomeDispositionTerminalNonSuccess}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "approved",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want terminal non-success leaf rejection")
	}
}

func TestSolutionPackageRejectsContinuationAndTerminalSuccessAtSink(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:2]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{DecisionRequirements: []OutcomeRequirement{{
		RequirementID: "design-approved",
		Outcomes:      []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{
		GateRequirements: []OutcomeRequirement{{
			RequirementID: "route",
			Outcomes:      []OutcomeDefinition{{Value: "go", Disposition: OutcomeDispositionContinuation}},
		}},
		DecisionRequirements: []OutcomeRequirement{{
			RequirementID: "result",
			Outcomes:      []OutcomeDefinition{{Value: "done", Disposition: OutcomeDispositionTerminalSuccess}},
		}},
	}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "approved",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want continuation sink rejection")
	}
}

func TestSolutionPackageRejectsUnresolvedActivationMaskedByAlternateSuccess(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "delivery-dag", ToNodeID: "change-design"},
	}
	alternate := pkg.BaselinePlanTemplate.Nodes[2]
	alternate.NodeID = "alternate-success"
	alternate.Display = display("Alternate Success")
	pkg.BaselinePlanTemplate.Nodes = append(pkg.BaselinePlanTemplate.Nodes, alternate)
	pkg.BaselinePlanTemplate.Edges = append(pkg.BaselinePlanTemplate.Edges, PlanEdge{FromNodeID: "solution-design", ToNodeID: "alternate-success"})
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "delivery", Disposition: OutcomeDispositionContinuation},
			{Value: "alternate", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "ready",
		Outcomes:      []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionContinuation}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "delivery",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "delivery-dag", RequirementID: "ready", Kind: RequirementKindGate, Outcome: "yes",
	}}}
	pkg.BaselinePlanTemplate.Nodes[3].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "alternate",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want unresolved activation rejection")
	}
}

func TestSolutionPackageAllowsTerminalNonSuccessAlternativeAndSuccessfulContinuation(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:2]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{{FromNodeID: "solution-design", ToNodeID: "delivery-dag"}}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "failed", Disposition: OutcomeDispositionTerminalNonSuccess},
			{Value: "deliver", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindGate, Outcome: "deliver",
	}}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v, want terminal failure alternative with successful continuation", err)
	}
}

func TestSolutionPackageRejectsGlobalMixedTerminalOutcomes(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:2]
	pkg.BaselinePlanTemplate.Edges = nil
	pkg.BaselinePlanTemplate.Nodes[1].InputBindings = nil
	pkg.BaselinePlanTemplate.Nodes[1].Activation = nil
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "done",
		Outcomes:      []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionTerminalSuccess}},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "failed",
		Outcomes:      []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionTerminalNonSuccess}},
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want mixed global terminal rejection")
	}
}

func TestSolutionPackageRejectsContinuationWithOnlyUnrelatedSuccessfulPath(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "solution-design", ToNodeID: "change-design"},
	}
	failedRoute := pkg.BaselinePlanTemplate.Nodes[2]
	failedRoute.NodeID = "failed-stop"
	failedRoute.Display = display("Failed Stop")
	pkg.BaselinePlanTemplate.Nodes = append(pkg.BaselinePlanTemplate.Nodes, failedRoute)
	pkg.BaselinePlanTemplate.Edges = append(pkg.BaselinePlanTemplate.Edges, PlanEdge{FromNodeID: "solution-design", ToNodeID: "failed-stop"})
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{
		GateRequirements: []OutcomeRequirement{{
			RequirementID: "always",
			Outcomes:      []OutcomeDefinition{{Value: "go", Disposition: OutcomeDispositionContinuation}},
		}},
		DecisionRequirements: []OutcomeRequirement{{
			RequirementID: "route",
			Outcomes: []OutcomeDefinition{
				{Value: "go", Disposition: OutcomeDispositionContinuation},
				{Value: "stop", Disposition: OutcomeDispositionContinuation},
			},
		}},
	}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{}
	packageContent(&pkg, "change-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "failed",
		Outcomes:      []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionTerminalNonSuccess}},
	}}}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "always", Kind: RequirementKindGate, Outcome: "go",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindDecision, Outcome: "go",
	}}}
	pkg.BaselinePlanTemplate.Nodes[3].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "route", Kind: RequirementKindDecision, Outcome: "stop",
	}}}

	if err := pkg.Validate(); err == nil {
		t.Fatal("Validate() error = nil, want unrelated success rejection")
	}
}

func TestSolutionPackageAllowsSelectedGraphLeafWithObsoleteStructuralSuccessor(t *testing.T) {
	pkg := completeSolutionPackage()
	pkg.BaselinePlanTemplate.Nodes = pkg.BaselinePlanTemplate.Nodes[:3]
	routerHarness := PackageContent{Kind: ContentKindHarnessDefinition, ContentID: "router-harness", ContentDigest: testDigest('9'), PackagePath: "contents/harnesses/router"}
	routerHarness.HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "route",
		Outcomes: []OutcomeDefinition{
			{Value: "x", Disposition: OutcomeDispositionContinuation},
			{Value: "y", Disposition: OutcomeDispositionContinuation},
		},
	}}}
	pkg.PackageContents = append(pkg.PackageContents, routerHarness)
	router := pkg.BaselinePlanTemplate.Nodes[2]
	router.NodeID = "router"
	router.Display = display("Router")
	router.Activation = nil
	router.InputBindings = nil
	router.Harness = ContentReference{Local: localReference(routerHarness)}
	success := router
	success.NodeID = "alternate-success"
	success.Display = display("Alternate Success")
	success.Harness = ContentReference{Local: localReference(*packageContent(&pkg, "change-harness"))}
	pkg.BaselinePlanTemplate.Nodes = append(pkg.BaselinePlanTemplate.Nodes, router, success)
	pkg.BaselinePlanTemplate.Edges = []PlanEdge{
		{FromNodeID: "solution-design", ToNodeID: "delivery-dag"},
		{FromNodeID: "delivery-dag", ToNodeID: "change-design"},
		{FromNodeID: "router", ToNodeID: "change-design"},
		{FromNodeID: "router", ToNodeID: "alternate-success"},
	}
	packageContent(&pkg, "solution-design-harness").HarnessDefinition = &HarnessDefinition{GateRequirements: []OutcomeRequirement{{
		RequirementID: "start",
		Outcomes:      []OutcomeDefinition{{Value: "go", Disposition: OutcomeDispositionContinuation}},
	}}}
	packageContent(&pkg, "delivery-harness").HarnessDefinition = &HarnessDefinition{}
	packageContent(&pkg, "change-harness").HarnessDefinition = &HarnessDefinition{}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "solution-design", RequirementID: "start", Kind: RequirementKindGate, Outcome: "go",
	}}}
	pkg.BaselinePlanTemplate.Nodes[2].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "router", RequirementID: "route", Kind: RequirementKindGate, Outcome: "x",
	}}}
	pkg.BaselinePlanTemplate.Nodes[3].Activation = nil
	pkg.BaselinePlanTemplate.Nodes[4].Activation = &Activation{Requirements: []ActivationRequirement{{
		FromNodeID: "router", RequirementID: "route", Kind: RequirementKindGate, Outcome: "y",
	}}}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v, want selected-graph leaf acceptance", err)
	}
}

func TestOutcomeAssignmentSolverRespectsForeignConstraintsAndBudget(t *testing.T) {
	foreign := activationKey{FromNodeID: "other-source", RequirementID: "other-gate", Kind: RequirementKindGate}
	local := activationKey{FromNodeID: "local-source", RequirementID: "local-gate", Kind: RequirementKindGate}
	assignment := outcomeAssignment{foreign: {Value: "yes", Disposition: OutcomeDispositionContinuation}, local: {Value: "go", Disposition: OutcomeDispositionContinuation}}
	node := PlanNodeTemplate{Activation: &Activation{Requirements: []ActivationRequirement{
		{FromNodeID: "other-source", RequirementID: "other-gate", Kind: RequirementKindGate, Outcome: "yes"},
		{FromNodeID: "local-source", RequirementID: "local-gate", Kind: RequirementKindGate, Outcome: "go"},
	}}}
	if !activationMatchesAssignment(node, assignment) {
		t.Fatal("activationMatchesAssignment() = false, want true for matching foreign constraint")
	}
	assignment[foreign] = OutcomeDefinition{Value: "no", Disposition: OutcomeDispositionContinuation}
	if activationMatchesAssignment(node, assignment) {
		t.Fatal("activationMatchesAssignment() = true, want false for foreign mismatch")
	}

	requirements := make([]sourceOutcomeRequirement, 10)
	for index := range requirements {
		requirements[index] = sourceOutcomeRequirement{key: activationKey{FromNodeID: "source", RequirementID: string(rune('a' + index)), Kind: RequirementKindGate}, outcomes: []OutcomeDefinition{{Value: "left", Disposition: OutcomeDispositionContinuation}, {Value: "right", Disposition: OutcomeDispositionContinuation}}}
	}
	count := 0
	if err := forEachOutcomeAssignment(requirements, func(outcomeAssignment) error { count++; return nil }); err != nil || count != maxOutcomeAssignments {
		t.Fatalf("boundary enumeration = (%d, %v), want (%d, nil)", count, err, maxOutcomeAssignments)
	}
	requirements = append(requirements, requirements[0])
	if err := forEachOutcomeAssignment(requirements, func(outcomeAssignment) error { return nil }); err == nil {
		t.Fatal("one-over-budget enumeration error = nil")
	}
}

func TestOutcomeAssignmentTracksMixedTerminalDispositionsPerNode(t *testing.T) {
	assignment := outcomeAssignment{
		{FromNodeID: "a", RequirementID: "one", Kind: RequirementKindGate}: {Value: "yes", Disposition: OutcomeDispositionTerminalSuccess},
		{FromNodeID: "a", RequirementID: "two", Kind: RequirementKindGate}: {Value: "no", Disposition: OutcomeDispositionTerminalNonSuccess},
	}
	harness := &HarnessDefinition{GateRequirements: []OutcomeRequirement{
		{RequirementID: "one", Outcomes: []OutcomeDefinition{{Value: "yes", Disposition: OutcomeDispositionTerminalSuccess}}},
		{RequirementID: "two", Outcomes: []OutcomeDefinition{{Value: "no", Disposition: OutcomeDispositionTerminalNonSuccess}}},
	}}
	disposition := assignment.dispositionFor("a", harness)
	if !disposition.hasTerminalSuccess || !disposition.hasTerminalNonSuccess {
		t.Fatalf("disposition = %#v, want both terminal dispositions", disposition)
	}
}

func TestSolutionPackageRejectsRoundTwoReviewProbes(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*SolutionPackage)
	}{
		{"missing canonical contract", func(pkg *SolutionPackage) { packageContent(pkg, "solution-design-contract").WorkUnitContract = nil }},
		{"incompatible slots", func(pkg *SolutionPackage) {
			different := PackageContent{Kind: ContentKindDataContract, ContentID: "different", ContentDigest: testDigest('7'), PackagePath: "contents/data/different"}
			pkg.PackageContents = append(pkg.PackageContents, different)
			packageContent(pkg, "delivery-contract").WorkUnitContract.InputSlots[0].DataContract = ContentReference{Local: localReference(different)}
		}},
		{"contradictory decision outcomes", func(pkg *SolutionPackage) {
			activation := pkg.BaselinePlanTemplate.Nodes[1].Activation
			activation.Requirements = append(activation.Requirements, ActivationRequirement{FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "rejected"})
		}},
		{"missing required continuation", func(pkg *SolutionPackage) {
			packageContent(pkg, "solution-design-harness").HarnessDefinition.DecisionRequirements[0].Outcomes = append(packageContent(pkg, "solution-design-harness").HarnessDefinition.DecisionRequirements[0].Outcomes, OutcomeDefinition{Value: "deferred", Disposition: OutcomeDispositionContinuation})
		}},
		{"weakened slot capability", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Nodes[1].ResponsibilityRequirement.CapabilityRequirements = []string{"orchestration.dag"}
		}},
		{"unknown responsibility slot", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Nodes[1].ResponsibilityRequirement.SlotKey = "unknown"
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			pkg := completeSolutionPackage()
			test.mutate(&pkg)
			if err := pkg.Validate(); err == nil {
				t.Fatal("Validate() error = nil")
			}
		})
	}
}

func TestSolutionPackageValidatesResolvedExternalWorkUnitContractBinding(t *testing.T) {
	pkg := completeSolutionPackage()
	externalData := ExactPackageRef{ComponentID: "shared-data", Version: "1.0.0", ArtifactDigest: testDigest('8')}
	externalContract := WorkUnitContract{
		Display:     display("External Contract"),
		OutputSlots: []ContractSlot{{SlotKey: "design", Display: display("Design"), Required: true, DataContract: ContentReference{External: &externalData}}},
	}
	pkg.BaselinePlanTemplate.Nodes[0].WorkUnitContract = ContentReference{
		External: &ExactPackageRef{ComponentID: "shared-contract", Version: "1.0.0", ArtifactDigest: testDigest('9')},
		Resolved: &ResolvedContent{WorkUnitContract: &externalContract},
	}
	packageContent(&pkg, "delivery-contract").WorkUnitContract.InputSlots[0].DataContract = ContentReference{External: &externalData}

	if err := pkg.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestFormFieldValidationRulesAndDefaults(t *testing.T) {
	minimum, maximum := 1.0, 10.0
	minLength, maxLength := 2, 5
	valid := []FormField{
		{FieldKey: "text", Display: display("Text"), FieldType: FieldTypeText, DefaultValue: json.RawMessage(`"abc"`), Validation: &FormValidation{MinLength: &minLength, MaxLength: &maxLength, Pattern: "^[a-z]+$"}},
		{FieldKey: "number", Display: display("Number"), FieldType: FieldTypeNumber, DefaultValue: json.RawMessage(`2`), Validation: &FormValidation{Minimum: &minimum, Maximum: &maximum}},
		{FieldKey: "boolean", Display: display("Boolean"), FieldType: FieldTypeBoolean, DefaultValue: json.RawMessage(`true`)},
		{FieldKey: "choice", Display: display("Choice"), FieldType: FieldTypeChoice, DefaultValue: json.RawMessage(`"yes"`), Options: []FormOption{{Value: "yes", Display: display("Yes")}}},
	}
	for _, field := range valid {
		if err := field.validate(); err != nil {
			t.Fatalf("%s validation error = %v", field.FieldKey, err)
		}
	}
	invalid := []FormField{
		{FieldKey: "short", Display: display("Short"), FieldType: FieldTypeText, DefaultValue: json.RawMessage(`"a"`), Validation: &FormValidation{MinLength: intPointer(2)}},
		{FieldKey: "number", Display: display("Number"), FieldType: FieldTypeNumber, DefaultValue: json.RawMessage(`"no"`)},
		{FieldKey: "boolean", Display: display("Boolean"), FieldType: FieldTypeBoolean, DefaultValue: json.RawMessage(`"true"`)},
		{FieldKey: "unknown-choice", Display: display("Choice"), FieldType: FieldTypeChoice, DefaultValue: json.RawMessage(`"no"`), Options: []FormOption{{Value: "yes", Display: display("Yes")}}},
		{FieldKey: "json", Display: display("JSON"), FieldType: FieldTypeText, DefaultValue: json.RawMessage(`{`)},
		{FieldKey: "resource", Display: display("Resource"), FieldType: FieldTypeRepositoryBinding, DefaultValue: json.RawMessage(`"secret"`)},
		{FieldKey: "choice", Display: display("Choice"), FieldType: FieldTypeChoice, Options: []FormOption{{Value: "yes", Display: display("Yes")}, {Value: "yes", Display: display("Again")}}},
		{FieldKey: "pattern", Display: display("Pattern"), FieldType: FieldTypeText, Validation: &FormValidation{Pattern: "["}},
		{FieldKey: "range", Display: display("Range"), FieldType: FieldTypeNumber, Validation: &FormValidation{Minimum: float64Pointer(2), Maximum: float64Pointer(1)}},
	}
	for _, field := range invalid {
		if err := field.validate(); err == nil {
			t.Fatalf("%s validation error = nil", field.FieldKey)
		}
	}
}

func TestContentAndExperienceValidators(t *testing.T) {
	pkg := completeSolutionPackage()
	contents, err := contentIndex(pkg.PackageContents)
	if err != nil {
		t.Fatalf("contentIndex() error = %v", err)
	}
	if err := validateContents(contents); err != nil {
		t.Fatalf("validateContents() error = %v", err)
	}
	if err := validateBinding(pkg.BaselinePlanTemplate.Nodes[1], pkg.BaselinePlanTemplate.Nodes[0], pkg.BaselinePlanTemplate.Nodes[1].InputBindings[0], contents); err != nil {
		t.Fatalf("validateBinding() error = %v", err)
	}
	if _, err := contentIndex([]PackageContent{pkg.PackageContents[0], pkg.PackageContents[0]}); err == nil {
		t.Fatal("duplicate content error = nil")
	}
	experience := DeclarativeExperience{Views: []ExperienceView{{ID: "workflow", Display: display("Workflow")}}, Forms: []ExperienceForm{{ID: "decision", Display: display("Decision")}}, Layouts: []ExperienceLayout{{ID: "detail", Display: display("Detail")}}, Renderers: []ExperienceRenderer{{ID: "artifact", Display: display("Artifact")}}, Terminology: map[string]Display{"workflow": display("Delivery")}}
	if err := experience.validate(); err != nil {
		t.Fatalf("experience validation error = %v", err)
	}
	experience.Renderers[0].ID = ""
	if err := experience.validate(); err == nil {
		t.Fatal("experience validation error = nil")
	}
	if err := (DeclarativeExperience{Terminology: map[string]Display{"": display("Invalid")}}).validate(); err == nil {
		t.Fatal("blank terminology error = nil")
	}
}

func TestCanonicalValidatorFailurePaths(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*SolutionPackage)
	}{
		{"work content without payload", func(pkg *SolutionPackage) { packageContent(pkg, "solution-design-contract").WorkUnitContract = nil }},
		{"harness content without payload", func(pkg *SolutionPackage) { packageContent(pkg, "solution-design-harness").HarnessDefinition = nil }},
		{"data content with payload", func(pkg *SolutionPackage) {
			packageContent(pkg, "design-document").WorkUnitContract = &WorkUnitContract{}
		}},
		{"duplicate contract slot", func(pkg *SolutionPackage) {
			contract := packageContent(pkg, "solution-design-contract").WorkUnitContract
			contract.OutputSlots = append(contract.OutputSlots, contract.OutputSlots[0])
		}},
		{"duplicate harness outcome", func(pkg *SolutionPackage) {
			packageContent(pkg, "solution-design-harness").HarnessDefinition.DecisionRequirements[0].Outcomes = []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}, {Value: "approved", Disposition: OutcomeDispositionContinuation}}
		}},
		{"duplicate activation", func(pkg *SolutionPackage) {
			activation := pkg.BaselinePlanTemplate.Nodes[1].Activation
			activation.Requirements = append(activation.Requirements, activation.Requirements[0])
		}},
		{"activation from nonupstream node", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Nodes[2].Activation.Requirements[0].FromNodeID = "delivery-dag"
		}},
		{"unresolved external contract", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Nodes[0].WorkUnitContract = ContentReference{External: &ExactPackageRef{ComponentID: "external", Version: "1.0.0", ArtifactDigest: testDigest('a')}}
		}},
		{"unresolved external harness", func(pkg *SolutionPackage) {
			pkg.BaselinePlanTemplate.Nodes[0].Harness = ContentReference{External: &ExactPackageRef{ComponentID: "external-harness", Version: "1.0.0", ArtifactDigest: testDigest('b')}}
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			pkg := completeSolutionPackage()
			test.mutate(&pkg)
			if err := pkg.Validate(); err == nil {
				t.Fatal("Validate() error = nil")
			}
		})
	}

}

func TestCanonicalResolverAndRequirementHelpers(t *testing.T) {
	pkg := completeSolutionPackage()
	contents, err := contentIndex(pkg.PackageContents)
	if err != nil {
		t.Fatalf("contentIndex() error = %v", err)
	}
	if _, err := resolveWorkUnitContract(pkg.BaselinePlanTemplate.Nodes[0].WorkUnitContract, contents); err != nil {
		t.Fatalf("resolveWorkUnitContract() error = %v", err)
	}
	if _, err := resolveHarnessDefinition(pkg.BaselinePlanTemplate.Nodes[0].Harness, contents); err != nil {
		t.Fatalf("resolveHarnessDefinition() error = %v", err)
	}
	externalContract := WorkUnitContract{Display: display("External"), OutputSlots: []ContractSlot{{SlotKey: "out", Display: display("Out"), Required: true, DataContract: ContentReference{External: &ExactPackageRef{ComponentID: "data", Version: "1.0.0", ArtifactDigest: testDigest('a')}}}}}
	if _, err := resolveWorkUnitContract(ContentReference{External: &ExactPackageRef{ComponentID: "contract", Version: "1.0.0", ArtifactDigest: testDigest('b')}, Resolved: &ResolvedContent{WorkUnitContract: &externalContract}}, contents); err != nil {
		t.Fatalf("resolved external contract error = %v", err)
	}
	externalHarness := HarnessDefinition{GateRequirements: []OutcomeRequirement{{RequirementID: "gate", Outcomes: []OutcomeDefinition{{Value: "pass", Disposition: OutcomeDispositionTerminalSuccess}}}}}
	if _, err := resolveHarnessDefinition(ContentReference{External: &ExactPackageRef{ComponentID: "harness", Version: "1.0.0", ArtifactDigest: testDigest('c')}, Resolved: &ResolvedContent{HarnessDefinition: &externalHarness}}, contents); err != nil {
		t.Fatalf("resolved external harness error = %v", err)
	}
	if err := (HarnessDefinition{GateRequirements: []OutcomeRequirement{{RequirementID: "", Outcomes: []OutcomeDefinition{{Value: "pass", Disposition: OutcomeDispositionTerminalSuccess}}}}}).validate(); err == nil {
		t.Fatal("invalid harness requirement error = nil")
	}
	if err := (HarnessDefinition{GateRequirements: []OutcomeRequirement{{RequirementID: "gate", Outcomes: []OutcomeDefinition{{Value: "pass", Disposition: "invalid"}}}}}).validate(); err == nil {
		t.Fatal("invalid outcome disposition error = nil")
	}
	if err := (HarnessDefinition{GateRequirements: []OutcomeRequirement{{RequirementID: "gate", Outcomes: []OutcomeDefinition{{Value: "failed", Disposition: OutcomeDispositionTerminalSuccess}}}}}).validate(); err != nil {
		t.Fatalf("typed terminal success validation error = %v", err)
	}
	if _, found := externalHarness.requirement(RequirementKindGate, "missing"); found {
		t.Fatal("missing requirement found")
	}
	if _, found := externalHarness.requirement("invalid", "gate"); found {
		t.Fatal("invalid requirement kind found")
	}
	contract := *packageContent(&pkg, "solution-design-contract").WorkUnitContract
	contract.OutputSlots = nil
	if err := contract.validate(contents); err == nil {
		t.Fatal("missing output slots error = nil")
	}
	contract = *packageContent(&pkg, "solution-design-contract").WorkUnitContract
	contract.OutputSlots[0].SlotKey = ""
	if err := contract.validate(contents); err == nil {
		t.Fatal("blank slot key error = nil")
	}
	contract = *packageContent(&pkg, "solution-design-contract").WorkUnitContract
	contract.OutputSlots[0].Required = false
	if err := ValidateWorkUnitContract(contract); err == nil {
		t.Fatal("all-optional output slots error = nil")
	}
	if sameReference(ContentReference{Local: localReference(pkg.PackageContents[0])}, ContentReference{External: &ExactPackageRef{ComponentID: "data", Version: "1.0.0", ArtifactDigest: testDigest('a')}}) {
		t.Fatal("mixed references matched")
	}
	if _, found := findSlot(nil, "missing"); found {
		t.Fatal("missing slot found")
	}
}

func completeSolutionPackage() SolutionPackage {
	pkg := baseSolutionPackage()
	data := PackageContent{Kind: ContentKindDataContract, ContentID: "design-document", ContentDigest: testDigest('1'), PackagePath: "contents/data/design-document"}
	firstContract := PackageContent{Kind: ContentKindWorkUnitContract, ContentID: "solution-design-contract", ContentDigest: testDigest('2'), PackagePath: "contents/contracts/solution-design"}
	secondContract := PackageContent{Kind: ContentKindWorkUnitContract, ContentID: "delivery-contract", ContentDigest: testDigest('3'), PackagePath: "contents/contracts/delivery"}
	thirdContract := PackageContent{Kind: ContentKindWorkUnitContract, ContentID: "change-contract", ContentDigest: testDigest('4'), PackagePath: "contents/contracts/change"}
	firstHarness := PackageContent{Kind: ContentKindHarnessDefinition, ContentID: "solution-design-harness", ContentDigest: testDigest('5'), PackagePath: "contents/harnesses/solution-design"}
	secondHarness := PackageContent{Kind: ContentKindHarnessDefinition, ContentID: "delivery-harness", ContentDigest: testDigest('6'), PackagePath: "contents/harnesses/delivery"}
	thirdHarness := PackageContent{Kind: ContentKindHarnessDefinition, ContentID: "change-harness", ContentDigest: testDigest('7'), PackagePath: "contents/harnesses/change"}
	fixture := PackageContent{Kind: ContentKindVerificationFixture, ContentID: "fixture-golden", ContentDigest: testDigest('8'), PackagePath: "contents/fixtures/golden"}
	firstContract.WorkUnitContract = &WorkUnitContract{Display: display("Solution Design Contract"), OutputSlots: []ContractSlot{{SlotKey: "design", Display: display("Design"), Required: true, DataContract: ContentReference{Local: localReference(data)}}}}
	secondContract.WorkUnitContract = &WorkUnitContract{Display: display("Delivery Contract"), InputSlots: []ContractSlot{{SlotKey: "design", Display: display("Design"), Required: true, DataContract: ContentReference{Local: localReference(data)}}}, OutputSlots: []ContractSlot{{SlotKey: "plan", Display: display("Plan"), Required: true, DataContract: ContentReference{Local: localReference(data)}}}}
	thirdContract.WorkUnitContract = &WorkUnitContract{Display: display("Change Contract"), OutputSlots: []ContractSlot{{SlotKey: "change", Display: display("Change"), Required: true, DataContract: ContentReference{Local: localReference(data)}}}}
	firstHarness.HarnessDefinition = &HarnessDefinition{DecisionRequirements: []OutcomeRequirement{{RequirementID: "design-approved", Outcomes: []OutcomeDefinition{{Value: "approved", Disposition: OutcomeDispositionContinuation}, {Value: "rejected", Disposition: OutcomeDispositionContinuation}}}}}
	secondHarness.HarnessDefinition = &HarnessDefinition{}
	thirdHarness.HarnessDefinition = &HarnessDefinition{}
	pkg.PackageContents = []PackageContent{data, firstContract, secondContract, thirdContract, firstHarness, secondHarness, thirdHarness, fixture}
	pkg.BaselinePlanTemplate.Nodes[0].WorkUnitContract = ContentReference{Local: localReference(firstContract)}
	pkg.BaselinePlanTemplate.Nodes[0].Harness = ContentReference{Local: localReference(firstHarness)}
	pkg.BaselinePlanTemplate.Nodes[1].WorkUnitContract = ContentReference{Local: localReference(secondContract)}
	pkg.BaselinePlanTemplate.Nodes[1].Harness = ContentReference{Local: localReference(secondHarness)}
	pkg.BaselinePlanTemplate.Nodes[1].ResponsibilityRequirement.CapabilityRequirements = []string{"architecture-design", "orchestration.dag"}
	pkg.BaselinePlanTemplate.Nodes[1].Activation = &Activation{Requirements: []ActivationRequirement{{FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "approved"}}}
	pkg.BaselinePlanTemplate.Nodes = append(pkg.BaselinePlanTemplate.Nodes, PlanNodeTemplate{NodeID: "change-design", Display: display("Change Design"), WorkUnitContract: ContentReference{Local: localReference(thirdContract)}, ResponsibilityRequirement: ResponsibilityRequirement{SlotKey: "architect", CapabilityRequirements: []string{"architecture-design"}}, Activation: &Activation{Requirements: []ActivationRequirement{{FromNodeID: "solution-design", RequirementID: "design-approved", Kind: RequirementKindDecision, Outcome: "rejected"}}}, Execution: Execution{Kind: ExecutionKindExecutor, Executor: &ExecutorExecution{CapabilityRequirements: []string{"architecture-design"}}}, Harness: ContentReference{Local: localReference(thirdHarness)}})
	pkg.BaselinePlanTemplate.Edges = append(pkg.BaselinePlanTemplate.Edges, PlanEdge{FromNodeID: "solution-design", ToNodeID: "change-design"})
	pkg.VerificationFixtures = []ContentReference{{Local: localReference(fixture)}}
	pkg.Experience = &DeclarativeExperience{Terminology: map[string]Display{"workflow": display("Delivery")}}
	return pkg
}

func packageContent(pkg *SolutionPackage, identifier string) *PackageContent {
	for index := range pkg.PackageContents {
		if pkg.PackageContents[index].ContentID == identifier {
			return &pkg.PackageContents[index]
		}
	}
	panic("missing test content " + identifier)
}

func terminalOutcomeRequirements(prefix string, count int) []OutcomeRequirement {
	requirements := make([]OutcomeRequirement, count)
	for index := range requirements {
		requirements[index] = OutcomeRequirement{
			RequirementID: prefix + "-gate-" + string(rune('a'+index)),
			Outcomes: []OutcomeDefinition{
				{Value: "left", Disposition: OutcomeDispositionTerminalSuccess},
				{Value: "right", Disposition: OutcomeDispositionTerminalSuccess},
			},
		}
	}
	return requirements
}

func localReference(content PackageContent) *LocalContentReference {
	return &LocalContentReference{ContentID: content.ContentID, ContentDigest: content.ContentDigest, PackagePath: content.PackagePath}
}

func typedPayloadCount(value any, key string) int {
	switch value := value.(type) {
	case map[string]any:
		count := 0
		for currentKey, child := range value {
			if currentKey == key && isTypedPayload(child) {
				count++
			}
			count += typedPayloadCount(child, key)
		}
		return count
	case []any:
		count := 0
		for _, child := range value {
			count += typedPayloadCount(child, key)
		}
		return count
	default:
		return 0
	}
}

func wireKeyCount(value any, key string) int {
	switch value := value.(type) {
	case map[string]any:
		count := 0
		for currentKey, child := range value {
			if currentKey == key {
				count++
			}
			count += wireKeyCount(child, key)
		}
		return count
	case []any:
		count := 0
		for _, child := range value {
			count += wireKeyCount(child, key)
		}
		return count
	default:
		return 0
	}
}

func mustMarshal(t *testing.T, value any) []byte {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	return encoded
}

func isTypedPayload(value any) bool {
	payload, ok := value.(map[string]any)
	if !ok {
		return false
	}
	_, hasDisplay := payload["display"]
	return hasDisplay
}

func float64Pointer(value float64) *float64 { return &value }
func intPointer(value int) *int             { return &value }

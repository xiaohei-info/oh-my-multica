package extension_test

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/xiaohei-info/open-agent-cluster/contracts/extension"
)

func digest(character string) string {
	return "sha256:" + strings.Repeat(character, 64)
}

func validEnvelope() extension.PackageEnvelope {
	return extension.PackageEnvelope{
		PackageType:    extension.PackageTypeExtension,
		PackageID:      "harness-guides",
		PackageVersion: "1.4.0",
		InterfaceAPI:   "harness.oac.dev/v1",
		Entrypoint: &extension.Entrypoint{
			Command: "bin/harness-guides",
			Args:    []string{"--serve"},
		},
		Origin:        "https://github.com/acme/harness-guides",
		PackageSource: "registry.oac.dev",
		Signature:     "sig-abc",
	}
}

func validBinding() extension.HarnessExtensionBinding {
	return extension.HarnessExtensionBinding{
		BindingID:        "binding-01",
		ExtensionPackage: extension.ExactPackageRef{PackageType: extension.PackageTypeExtension, PackageID: "harness-guides", PackageVersion: "1.4.0", ArtifactDigest: digest("a")},
		ArtifactDigest:   digest("a"),
		ConfigDigest:     digest("b"),
		ExtensionID:      "guide-001",
		Required:         true,
		Subject:          extension.SubjectKey{Kind: "work_unit", ID: "wu-1", Digest: digest("c")},
	}
}

func validContext() extension.RunRequestContext {
	context := extension.RunRequestContext{
		WorkflowID:     "workflow-1",
		WorkUnitID:     "wu-1",
		Iteration:      3,
		ComponentRunID: "run-1",
		BindingID:      "binding-01",
		Subject:        extension.SubjectKey{Kind: "work_unit", ID: "wu-1", Digest: digest("c")},
	}
	digest, err := context.CanonicalDigest()
	if err != nil {
		panic(err)
	}
	context.RequestDigest = digest
	return context
}

func validRun() extension.ComponentRun {
	return extension.ComponentRun{
		ComponentRunID:   "run-1",
		ExtensionPackage: extension.ExactPackageRef{PackageType: extension.PackageTypeExtension, PackageID: "deploy-tool", PackageVersion: "2.1.0", ArtifactDigest: digest("a")},
		ArtifactDigest:   digest("a"),
		WorkflowID:       "workflow-1",
		WorkUnitID:       "wu-1",
		Iteration:        3,
		Attempt:          1,
		ExtensionID:      "executor-001",
		HarnessBindingID: "binding-01",
		Subject:          extension.SubjectKey{Kind: "work_unit", ID: "wu-1", Digest: digest("c")},
		IdempotencyKey:   "idem-1",
		RequestDigest:    digest("d"),
		Status:           extension.ComponentRunRunning,
	}
}

func validExtensionPackage() extension.ExtensionPackage {
	return extension.ExtensionPackage{
		Package:       validEnvelope(),
		ExecutionMode: extension.ExecutionModeService,
		Extensions:    []extension.Extension{validExtension()},
	}
}

func TestExtensionWireIdentityDigestAndTaggedState(t *testing.T) {
	envelope := validEnvelope()
	if err := envelope.Validate(); err != nil {
		t.Fatalf("valid envelope: %v", err)
	}
	if envelope.Identity().IdentityKey() != "extension/harness-guides@1.4.0" {
		t.Fatalf("identity key = %q", envelope.Identity().IdentityKey())
	}
	if err := envelope.Identity().Validate(); err != nil {
		t.Fatalf("declared package identity: %v", err)
	}
	if err := (extension.PackageIdentity{}).Validate(); err == nil {
		t.Fatal("empty package identity accepted")
	}
	if err := (&extension.Entrypoint{}).Validate(); err == nil {
		t.Fatal("empty entrypoint accepted")
	}
	content := extension.PackageEnvelope{
		PackageType: extension.PackageTypeContent, PackageID: "skill", PackageVersion: "1.0.0",
		DescriptorPath: "package.yaml", Origin: "github", PackageSource: "registry",
	}
	if err := content.Validate(); err != nil {
		t.Fatalf("valid content envelope: %v", err)
	}
	if _, err := content.CanonicalDigest(); err != nil {
		t.Fatalf("content canonical digest: %v", err)
	}
	firstDigest, err := envelope.CanonicalDigest()
	if err != nil {
		t.Fatalf("canonical digest: %v", err)
	}
	again, err := envelope.CanonicalDigest()
	if err != nil {
		t.Fatalf("canonical digest again: %v", err)
	}
	if firstDigest != again {
		t.Fatalf("canonical digest is not deterministic")
	}

	binding := validBinding()
	if err := binding.Validate(); err != nil {
		t.Fatalf("valid binding: %v", err)
	}
	bindingDigest, err := binding.BindingDigest()
	if err != nil {
		t.Fatalf("binding digest: %v", err)
	}
	binding.ConfigDigest = digest("d")
	changedDigest, err := binding.BindingDigest()
	if err != nil {
		t.Fatalf("binding digest after change: %v", err)
	}
	if changedDigest == bindingDigest {
		t.Fatal("binding digest did not change when config digest changed")
	}
	binding.ConfigDigest = digest("b")
	binding.Required = false
	optionalDigest, err := binding.BindingDigest()
	if err != nil {
		t.Fatalf("optional binding digest: %v", err)
	}
	if optionalDigest == bindingDigest {
		t.Fatal("binding digest did not change when Required changed")
	}

	exact := binding.ExtensionPackage
	if exact.IdentityKey() != "extension/harness-guides@1.4.0#"+digest("a") {
		t.Fatalf("exact identity key = %q", exact.IdentityKey())
	}
	if err := exact.Validate(); err != nil {
		t.Fatalf("exact package ref: %v", err)
	}
	if _, err := exact.CanonicalDigest(); err != nil {
		t.Fatalf("exact package canonical digest: %v", err)
	}
	if _, err := (extension.ExactPackageRef{}).CanonicalDigest(); err == nil {
		t.Fatal("empty exact package ref canonicalized")
	}

	context := validContext()
	if err := context.ValidateStructure(); err != nil {
		t.Fatalf("valid context structure: %v", err)
	}
	if err := context.Validate(); err == nil || !strings.Contains(err.Error(), "host provenance") {
		t.Fatalf("caller-computed context accepted as host authority: %v", err)
	}

	// Tagged unions round-trip through the wire with their kind tags intact.
	external := extension.ExternalOperation{SystemType: "github.pull-request", ExternalID: "pr-42", URI: "https://github.com/acme/repo/pull/42"}
	encoded, err := json.Marshal(external)
	if err != nil {
		t.Fatalf("marshal external operation: %v", err)
	}
	var decodedExternal extension.ExternalOperation
	if err := json.Unmarshal(encoded, &decodedExternal); err != nil {
		t.Fatalf("unmarshal external operation: %v", err)
	}
	if decodedExternal != external {
		t.Fatalf("external operation round trip = %#v, want %#v", decodedExternal, external)
	}

	result := extension.ComponentRunResult{Kind: extension.ResultArtifact, ArtifactID: "artifact-7", Digest: digest("e")}
	encodedResult, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encodedResult, &fields); err != nil {
		t.Fatalf("unmarshal result fields: %v", err)
	}
	if string(fields["kind"]) != `"artifact"` {
		t.Fatalf("result kind tag = %s", fields["kind"])
	}
	var decodedResult extension.ComponentRunResult
	if err := json.Unmarshal(encodedResult, &decodedResult); err != nil {
		t.Fatalf("unmarshal result: %v", err)
	}
	if decodedResult != result {
		t.Fatalf("result round trip = %#v, want %#v", decodedResult, result)
	}

	run := validRun()
	run.Status = extension.ComponentRunSucceeded
	run.Result = &result
	run.ExternalOperation = &external
	runEncoded, err := json.Marshal(run)
	if err != nil {
		t.Fatalf("marshal run: %v", err)
	}
	var runFields map[string]json.RawMessage
	if err := json.Unmarshal(runEncoded, &runFields); err != nil {
		t.Fatalf("unmarshal run fields: %v", err)
	}
	if _, ok := runFields["extension_package"]; !ok {
		t.Fatal("component run wire is missing extension_package")
	}
	if _, ok := runFields["package"]; ok {
		t.Fatal("component run wire still exposes generic package")
	}
	for name, value := range map[string]any{
		"binding": validBinding(),
		"gate":    extension.GateEvaluatorRegistration{Display: extension.Display{Name: "Gate", Description: "pure"}, ExtensionPackage: extension.ExactPackageRef{PackageType: extension.PackageTypeExtension, PackageID: "gate", PackageVersion: "1.0.0", ArtifactDigest: digest("f")}, ReleaseDigest: digest("f")},
	} {
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatalf("marshal %s: %v", name, err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(encoded, &fields); err != nil {
			t.Fatalf("unmarshal %s: %v", name, err)
		}
		if _, ok := fields["extension_package"]; !ok {
			t.Fatalf("%s wire is missing extension_package", name)
		}
		if _, ok := fields["package"]; ok {
			t.Fatalf("%s wire still exposes generic package", name)
		}
	}
	errorWire, err := json.Marshal(extension.ComponentRunError{Class: "external", Retryable: true, OutcomeKnown: false})
	if err != nil {
		t.Fatalf("marshal component error: %v", err)
	}
	var errorFields map[string]json.RawMessage
	if err := json.Unmarshal(errorWire, &errorFields); err != nil {
		t.Fatalf("unmarshal component error: %v", err)
	}
	if _, ok := errorFields["retry_proven"]; ok {
		t.Fatal("untrusted retry proof leaked into component error wire")
	}
	var decodedRun extension.ComponentRun
	if err := json.Unmarshal(runEncoded, &decodedRun); err != nil {
		t.Fatalf("unmarshal run: %v", err)
	}
	if !reflect.DeepEqual(decodedRun, run) {
		t.Fatalf("run round trip = %#v, want %#v", decodedRun, run)
	}
}

func TestExtensionWireValidatorRejectsCallerAuthorityClaims(t *testing.T) {
	validClaims := extension.CallerAuthorityClaims{}
	if err := validClaims.Validate(); err != nil {
		t.Fatalf("empty claims must pass: %v", err)
	}

	for _, test := range []struct {
		name   string
		claims extension.CallerAuthorityClaims
		want   string
	}{
		{"package root", extension.CallerAuthorityClaims{PackageRoot: "/opt/installed"}, "package root"},
		{"evaluator identity", extension.CallerAuthorityClaims{EvaluatorIdentity: "trusted-evaluator"}, "evaluator identity"},
		{"isolation flags", extension.CallerAuthorityClaims{Isolation: "privileged"}, "isolation"},
		{"permission ceiling", extension.CallerAuthorityClaims{PermissionCeiling: []extension.Permission{extension.PermissionAgentInvoke}}, "permission ceiling"},
		{"recovery claim", extension.CallerAuthorityClaims{Recovery: "safe-to-retry"}, "recovery"},
		{"lineage", extension.CallerAuthorityClaims{Lineage: "event-chain-1"}, "lineage"},
		{"request context", extension.CallerAuthorityClaims{RequestContext: &extension.RunRequestContext{}}, "request context"},
	} {
		t.Run(test.name, func(t *testing.T) {
			if err := test.claims.Validate(); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}

	// Wire types themselves fail closed on caller-authored authority.
	executorClaim := extension.Extension{
		ExtensionID:    "exec-1",
		Display:        extension.Display{Name: "Exec", Description: "exec"},
		ExtensionPoint: extension.ExtensionPointSensorComputational,
		EffectMode:     extension.EffectModePure,
		Permissions:    []extension.Permission{extension.PermissionAgentInvoke},
	}
	if err := executorClaim.Validate(); err == nil || !strings.Contains(err.Error(), "agent.invoke") {
		t.Fatalf("agent.invoke claim on sensor accepted: %v", err)
	}
	if err := validBinding().Validate(); err != nil {
		t.Fatalf("valid binding: %v", err)
	}
}

func TestComponentRunWirePreservesUnknownCancelRecoveryShape(t *testing.T) {
	if err := validRun().Validate(); err != nil {
		t.Fatalf("valid running run: %v", err)
	}

	for _, status := range []extension.ComponentRunStatus{
		extension.ComponentRunPending,
		extension.ComponentRunRunning,
		extension.ComponentRunSucceeded,
		extension.ComponentRunFailed,
		extension.ComponentRunUnknown,
		extension.ComponentRunCancelling,
		extension.ComponentRunCancelled,
	} {
		run := validRun()
		run.Status = status
		run.ExternalOperation = &extension.ExternalOperation{SystemType: "github.pull-request", ExternalID: "pr-9"}
		switch status {
		case extension.ComponentRunSucceeded:
			run.Result = &extension.ComponentRunResult{Kind: extension.ResultArtifact, ArtifactID: "artifact-9", Digest: digest("e")}
		case extension.ComponentRunFailed:
			run.Error = &extension.ComponentRunError{Class: "external", OutcomeKnown: true}
		}
		if err := run.Validate(); err != nil {
			t.Fatalf("status %s: %v", status, err)
		}
	}

	invalidStatus := validRun()
	invalidStatus.Status = "AlmostDone"
	if invalidStatus.Validate() == nil {
		t.Fatal("Validate() accepted a non-enum status")
	}

	unknownWithoutOperation := validRun()
	unknownWithoutOperation.Status = extension.ComponentRunUnknown
	if err := unknownWithoutOperation.Validate(); err == nil || !strings.Contains(err.Error(), "external operation") {
		t.Fatalf("unknown run without external operation: %v", err)
	}
	invalidRecovery := extension.RecoveryFor(unknownWithoutOperation)
	if invalidRecovery.RetryPermitted || !invalidRecovery.NeedsObserve || !invalidRecovery.Escalate {
		t.Fatalf("invalid unknown recovery = %#v", invalidRecovery)
	}

	recovery := extension.RecoveryFor(validRun())
	if recovery.NeedsObserve || recovery.Terminal || recovery.RetryPermitted {
		t.Fatalf("running run recovery = %#v, want all false", recovery)
	}

	for _, terminal := range []extension.ComponentRunStatus{
		extension.ComponentRunSucceeded,
		extension.ComponentRunFailed,
		extension.ComponentRunCancelled,
	} {
		run := validRun()
		run.Status = terminal
		switch terminal {
		case extension.ComponentRunSucceeded:
			run.Result = &extension.ComponentRunResult{Kind: extension.ResultArtifact, ArtifactID: "artifact-terminal", Digest: digest("e")}
		case extension.ComponentRunFailed:
			run.Error = &extension.ComponentRunError{Class: "external", OutcomeKnown: true}
		}
		decision := extension.RecoveryFor(run)
		if !decision.Terminal || decision.NeedsObserve || decision.RetryPermitted {
			t.Fatalf("%s recovery = %#v, want terminal without observe", terminal, decision)
		}
	}
	failedUnknown := validRun()
	failedUnknown.Status = extension.ComponentRunFailed
	failedUnknown.Error = &extension.ComponentRunError{Class: "external", OutcomeKnown: false}
	failedDecision := extension.RecoveryFor(failedUnknown)
	if failedDecision.Terminal || failedDecision.RetryPermitted {
		t.Fatalf("failed unknown-outcome run was terminalized: %#v", failedDecision)
	}

	unknown := validRun()
	unknown.Status = extension.ComponentRunUnknown
	unknown.Error = &extension.ComponentRunError{Class: "external", Retryable: true, OutcomeKnown: false}
	decision := extension.RecoveryFor(unknown)
	if !decision.NeedsObserve || decision.RetryPermitted || decision.Terminal {
		t.Fatalf("unknown unproven recovery = %#v", decision)
	}

	proven := validRun()
	proven.Status = extension.ComponentRunUnknown
	proven.ExternalOperation = &extension.ExternalOperation{SystemType: "github.pull-request", ExternalID: "pr-proven"}
	proven.Error = &extension.ComponentRunError{Class: "external", Retryable: true, OutcomeKnown: true}
	decision = extension.RecoveryFor(proven)
	if decision.RetryPermitted || !decision.NeedsObserve || !decision.Escalate {
		t.Fatalf("unknown public retry claim authorized retry = %#v", decision)
	}

	escalate := validRun()
	escalate.Status = extension.ComponentRunUnknown
	escalate.ExternalOperation = &extension.ExternalOperation{SystemType: "github.pull-request", ExternalID: "pr-escalate"}
	escalate.Error = &extension.ComponentRunError{Class: "timeout", OutcomeKnown: true}
	decision = extension.RecoveryFor(escalate)
	if !decision.Escalate || !decision.NeedsObserve || decision.RetryPermitted {
		t.Fatalf("unknown exhausted recovery = %#v, want escalate", decision)
	}

	cancelling := validRun()
	cancelling.Status = extension.ComponentRunCancelling
	decision = extension.RecoveryFor(cancelling)
	if !decision.NeedsObserve || decision.Terminal {
		t.Fatalf("cancelling recovery = %#v, want observe and not terminal", decision)
	}
}

func TestExtensionWireExternalPackageNegativeConformance(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*extension.PackageEnvelope)
		want   string
	}{
		{"unknown package type", func(envelope *extension.PackageEnvelope) { envelope.PackageType = "plugin" }, "package type"},
		{"empty package id", func(envelope *extension.PackageEnvelope) { envelope.PackageID = "" }, "package id"},
		{"non semver version", func(envelope *extension.PackageEnvelope) { envelope.PackageVersion = "latest" }, "semantic version"},
		{"extension missing interface", func(envelope *extension.PackageEnvelope) { envelope.InterfaceAPI = "" }, "interface api"},
		{"extension missing entrypoint", func(envelope *extension.PackageEnvelope) { envelope.Entrypoint = nil }, "entrypoint"},
		{"entrypoint absolute path", func(envelope *extension.PackageEnvelope) {
			envelope.Entrypoint = &extension.Entrypoint{Command: "/etc/passwd"}
		}, "package-relative"},
		{"entrypoint escape", func(envelope *extension.PackageEnvelope) {
			envelope.Entrypoint = &extension.Entrypoint{Command: "bin/../outside"}
		}, "escapes"},
		{"entrypoint arg escape", func(envelope *extension.PackageEnvelope) {
			envelope.Entrypoint = &extension.Entrypoint{Command: "bin/harness", Args: []string{"../../evil"}}
		}, "escapes"},
		{"solution declares interface", func(envelope *extension.PackageEnvelope) {
			envelope.PackageType = extension.PackageTypeSolution
			envelope.InterfaceAPI = "harness.oac.dev/v1"
		}, "interface api"},
		{"solution declares entrypoint", func(envelope *extension.PackageEnvelope) {
			envelope.PackageType = extension.PackageTypeContent
			envelope.InterfaceAPI = ""
			envelope.Entrypoint = &extension.Entrypoint{Command: "bin/x"}
		}, "entrypoint"},
		{"descriptor escapes", func(envelope *extension.PackageEnvelope) {
			envelope.DescriptorPath = "../../secrets"
		}, "escapes"},
		{"signature without origin", func(envelope *extension.PackageEnvelope) { envelope.Origin = "" }, "origin"},
		{"unsigned missing origin", func(envelope *extension.PackageEnvelope) { envelope.Signature = ""; envelope.Origin = "" }, "origin"},
		{"missing package source", func(envelope *extension.PackageEnvelope) { envelope.PackageSource = "" }, "package source"},
		{"unsupported interface", func(envelope *extension.PackageEnvelope) { envelope.InterfaceAPI = "not-supported.oac.dev/v1" }, "unsupported interface api"},
		{"leading-zero semver", func(envelope *extension.PackageEnvelope) { envelope.PackageVersion = "1.0.0-01" }, "semantic version"},
		{"windows descriptor escape", func(envelope *extension.PackageEnvelope) {
			envelope.PackageType = extension.PackageTypeContent
			envelope.InterfaceAPI = ""
			envelope.Entrypoint = nil
			envelope.DescriptorPath = `..\\secrets`
		}, "escapes"},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := validEnvelope()
			test.mutate(&candidate)
			if err := candidate.Validate(); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}

	if err := validExtensionPackage().Validate(); err != nil {
		t.Fatalf("valid ExtensionPackage: %v", err)
	}
	for _, test := range []struct {
		name   string
		mutate func(*extension.ExtensionPackage)
		want   string
	}{
		{"missing execution mode", func(pkg *extension.ExtensionPackage) { pkg.ExecutionMode = "" }, "execution mode"},
		{"unsupported execution mode", func(pkg *extension.ExtensionPackage) { pkg.ExecutionMode = "remote" }, "execution mode"},
		{"missing described extensions", func(pkg *extension.ExtensionPackage) { pkg.Extensions = nil }, "described extension"},
		{"duplicate extension id", func(pkg *extension.ExtensionPackage) { pkg.Extensions = append(pkg.Extensions, pkg.Extensions[0]) }, "duplicate extension id"},
		{"invalid described extension", func(pkg *extension.ExtensionPackage) { pkg.Extensions[0].ExtensionPoint = "harness.custom" }, "unsupported extension point"},
		{"wrong harness interface", func(pkg *extension.ExtensionPackage) { pkg.Package.InterfaceAPI = extension.InterfaceAPIRuntime }, "harness interface api"},
		{"non-extension package", func(pkg *extension.ExtensionPackage) {
			pkg.Package.PackageType = extension.PackageTypeContent
			pkg.Package.InterfaceAPI = ""
			pkg.Package.Entrypoint = nil
			pkg.Package.DescriptorPath = "package.yaml"
		}, "package type extension"},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := validExtensionPackage()
			test.mutate(&candidate)
			if err := candidate.Validate(); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}

	// Negative Harness Extension fixtures fail closed.
	for _, test := range []struct {
		name   string
		mutate func(*extension.Extension)
		want   string
	}{
		{"missing extension id", func(ext *extension.Extension) { ext.ExtensionID = "" }, "extension id"},
		{"missing display name", func(ext *extension.Extension) { ext.Display.Name = "" }, "display name"},
		{"unsupported extension point", func(ext *extension.Extension) { ext.ExtensionPoint = "harness.plugin.custom" }, "unsupported extension point"},
		{"invalid effect mode", func(ext *extension.Extension) { ext.EffectMode = "noisy" }, "effect mode"},
		{"side-effecting guide", func(ext *extension.Extension) {
			ext.ExtensionPoint = extension.ExtensionPointGuideComputational
			ext.EffectMode = extension.EffectModeSideEffecting
		}, "side-effecting"},
		{"side-effecting sensor", func(ext *extension.Extension) {
			ext.ExtensionPoint = extension.ExtensionPointSensorInferential
			ext.EffectMode = extension.EffectModeSideEffecting
		}, "side-effecting"},
		{"unknown permission", func(ext *extension.Extension) {
			ext.Permissions = []extension.Permission{"secret.list"}
		}, "permission"},
		{"missing permissions array", func(ext *extension.Extension) { ext.Permissions = nil }, "permissions array"},
		{"executor side-effecting allowed", func(ext *extension.Extension) {
			ext.ExtensionPoint = extension.ExtensionPointExecutor
			ext.EffectMode = extension.EffectModeSideEffecting
		}, ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := validExtension()
			test.mutate(&candidate)
			err := candidate.Validate()
			if test.want == "" {
				if err != nil {
					t.Fatalf("Validate() = %v, want success", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}
	noPermissions := validExtension()
	noPermissions.Permissions = []extension.Permission{}
	if err := noPermissions.Validate(); err != nil {
		t.Fatalf("empty permissions array should be valid: %v", err)
	}

	// External package binding and gate evaluator fixtures fail closed.
	badBinding := validBinding()
	badBinding.ArtifactDigest = "not-a-digest"
	if err := badBinding.Validate(); err == nil || !strings.Contains(err.Error(), "sha256") {
		t.Fatalf("binding digest validation: %v", err)
	}
	badBinding = validBinding()
	badBinding.ExtensionPackage.PackageVersion = "v2"
	if err := badBinding.Validate(); err == nil || !strings.Contains(err.Error(), "semantic version") {
		t.Fatalf("binding package version: %v", err)
	}
	badBinding = validBinding()
	badBinding.Required = false
	bindingDigest, err := badBinding.BindingDigest()
	if err != nil {
		t.Fatalf("optional binding digest: %v", err)
	}
	badBinding.Required = true
	requiredDigest, err := badBinding.BindingDigest()
	if err != nil {
		t.Fatalf("required binding digest: %v", err)
	}
	if bindingDigest == requiredDigest {
		t.Fatal("Required flag is not bound to the harness digest")
	}
	badRef := validBinding().ExtensionPackage
	badRef.ArtifactDigest = ""
	if err := badRef.Validate(); err == nil || !strings.Contains(err.Error(), "artifact digest") {
		t.Fatalf("exact package ref without artifact digest accepted: %v", err)
	}
	solutionBinding := validBinding()
	solutionBinding.ExtensionPackage.PackageType = extension.PackageTypeSolution
	if err := solutionBinding.Validate(); err == nil || !strings.Contains(err.Error(), "requires extension package") {
		t.Fatalf("solution package accepted as harness binding: %v", err)
	}

	gateEvaluator := extension.GateEvaluatorRegistration{
		Display:          extension.Display{Name: "NoSecrets", Description: "pure check"},
		ExtensionPackage: extension.ExactPackageRef{PackageType: extension.PackageTypeExtension, PackageID: "trusted-gate", PackageVersion: "1.0.0", ArtifactDigest: digest("f")},
		ReleaseDigest:    digest("f"),
	}
	if err := gateEvaluator.Validate(); err != nil {
		t.Fatalf("valid gate evaluator: %v", err)
	}
	solutionGate := gateEvaluator
	solutionGate.ExtensionPackage.PackageType = extension.PackageTypeSolution
	if err := solutionGate.Validate(); err == nil || !strings.Contains(err.Error(), "requires extension package") {
		t.Fatalf("solution package accepted as gate evaluator: %v", err)
	}
	solutionRun := validRun()
	solutionRun.ExtensionPackage.PackageType = extension.PackageTypeContent
	if err := solutionRun.Validate(); err == nil || !strings.Contains(err.Error(), "requires extension package") {
		t.Fatalf("content package accepted as component run: %v", err)
	}
	gateEvaluator.ExtensionPackage.ArtifactDigest = digest("e")
	if err := gateEvaluator.Validate(); err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Fatalf("mismatched evaluator digest accepted: %v", err)
	}
	gateEvaluator.ExtensionPackage.ArtifactDigest = digest("f")
	gateEvaluator.ReleaseDigest = ""
	if err := gateEvaluator.Validate(); err == nil || !strings.Contains(err.Error(), "release digest") {
		t.Fatalf("gate evaluator digest: %v", err)
	}

	// Caller-authored scoped context with a fabricated digest is rejected.
	fabricatedContext := extension.RunRequestContext{
		WorkflowID: "workflow-1", WorkUnitID: "wu-1", Iteration: 0, BindingID: "binding-01",
		Subject:       extension.SubjectKey{Kind: "work_unit", ID: "wu-1", Digest: digest("c")},
		RequestDigest: digest("9"),
	}
	if err := fabricatedContext.ValidateStructure(); err == nil || !strings.Contains(err.Error(), "request digest") {
		t.Fatalf("fabricated request digest accepted: %v", err)
	}
	fabricatedContext.RequestDigest = ""
	if err := fabricatedContext.ValidateStructure(); err == nil || !strings.Contains(err.Error(), "request digest") {
		t.Fatalf("missing request digest accepted: %v", err)
	}
	matchingContext := validContext()
	if err := matchingContext.Validate(); err == nil || !strings.Contains(err.Error(), "host provenance") {
		t.Fatalf("canonically matching caller context accepted: %v", err)
	}
}

func TestExtensionWireComponentRunNegativeFixtures(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*extension.ComponentRun)
		want   string
	}{
		{"missing run id", func(run *extension.ComponentRun) { run.ComponentRunID = "" }, "component run id"},
		{"missing idempotency key", func(run *extension.ComponentRun) { run.IdempotencyKey = "" }, "idempotency key"},
		{"missing artifact digest", func(run *extension.ComponentRun) { run.ArtifactDigest = "" }, "artifact digest"},
		{"missing binding id", func(run *extension.ComponentRun) { run.HarnessBindingID = "" }, "harness binding id"},
		{"missing workflow id", func(run *extension.ComponentRun) { run.WorkflowID = "" }, "workflow id"},
		{"missing work unit id", func(run *extension.ComponentRun) { run.WorkUnitID = "" }, "work unit id"},
		{"negative attempt", func(run *extension.ComponentRun) { run.Attempt = -1 }, "attempt"},
		{"missing request digest", func(run *extension.ComponentRun) { run.RequestDigest = "" }, "request digest"},
		{"package digest mismatch", func(run *extension.ComponentRun) { run.ExtensionPackage.ArtifactDigest = digest("b") }, "does not match"},
		{"missing extension id", func(run *extension.ComponentRun) { run.ExtensionID = "" }, "extension id"},
		{"missing subject id", func(run *extension.ComponentRun) { run.Subject.ID = "" }, "subject id"},
		{"invalid status", func(run *extension.ComponentRun) { run.Status = "Done" }, "status"},
		{"invalid external operation", func(run *extension.ComponentRun) {
			run.Status = extension.ComponentRunRunning
			run.ExternalOperation = &extension.ExternalOperation{SystemType: "", ExternalID: ""}
		}, "external operation system type"},
		{"invalid result kind", func(run *extension.ComponentRun) {
			run.Status = extension.ComponentRunSucceeded
			run.Result = &extension.ComponentRunResult{Kind: "summary", Digest: digest("e")}
		}, "result kind"},
		{"artifact result missing id", func(run *extension.ComponentRun) {
			run.Status = extension.ComponentRunSucceeded
			run.Result = &extension.ComponentRunResult{Kind: extension.ResultArtifact, Digest: digest("e")}
		}, "artifact result requires artifact id"},
		{"missing error class", func(run *extension.ComponentRun) {
			run.Status = extension.ComponentRunFailed
			run.Error = &extension.ComponentRunError{Retryable: true}
		}, "error class"},
		{"failed unknown outcome", func(run *extension.ComponentRun) {
			run.Status = extension.ComponentRunFailed
			run.Error = &extension.ComponentRunError{Class: "external", OutcomeKnown: false}
		}, "failed component run requires known outcome"},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := validRun()
			test.mutate(&candidate)
			if err := candidate.Validate(); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}

	for _, kind := range []extension.ComponentRunResultKind{
		extension.ResultObservation,
		extension.ResultReport,
		extension.ResultExternal,
	} {
		run := validRun()
		run.Status = extension.ComponentRunSucceeded
		result := extension.ComponentRunResult{Kind: kind, Digest: digest("e")}
		switch kind {
		case extension.ResultObservation:
			result.ObservationID = "obs-1"
		case extension.ResultReport:
			result.ReportID = "report-1"
		case extension.ResultExternal:
			result.ExternalID = "ext-1"
		}
		run.Result = &result
		if err := run.Validate(); err != nil {
			t.Fatalf("result kind %s: %v", kind, err)
		}
	}

	extraID := validRun()
	extraID.Status = extension.ComponentRunSucceeded
	extraID.Result = &extension.ComponentRunResult{Kind: extension.ResultArtifact, ArtifactID: "artifact-1", ReportID: "report-contradictory", Digest: digest("e")}
	if err := extraID.Validate(); err == nil || !strings.Contains(err.Error(), "unrelated payload") {
		t.Fatalf("result with unrelated ID accepted: %v", err)
	}
	failedWithoutError := validRun()
	failedWithoutError.Status = extension.ComponentRunFailed
	if err := failedWithoutError.Validate(); err == nil || !strings.Contains(err.Error(), "requires error") {
		t.Fatalf("failed run without error accepted: %v", err)
	}
	succeededWithoutResult := validRun()
	succeededWithoutResult.Status = extension.ComponentRunSucceeded
	if err := succeededWithoutResult.Validate(); err == nil || !strings.Contains(err.Error(), "requires result") {
		t.Fatalf("succeeded run without result accepted: %v", err)
	}
	inProgressWithResult := validRun()
	inProgressWithResult.Result = &extension.ComponentRunResult{Kind: extension.ResultArtifact, ArtifactID: "artifact-2", Digest: digest("e")}
	if err := inProgressWithResult.Validate(); err == nil || !strings.Contains(err.Error(), "in-progress") {
		t.Fatalf("in-progress run with result accepted: %v", err)
	}
}

func TestExtensionWireScopedContextAndBindingValidators(t *testing.T) {
	if err := validContext().ValidateStructure(); err != nil {
		t.Fatalf("valid context structure: %v", err)
	}

	for _, test := range []struct {
		name   string
		mutate func(*extension.RunRequestContext)
		want   string
	}{
		{"missing workflow", func(context *extension.RunRequestContext) { context.WorkflowID = "" }, "workflow id"},
		{"missing work unit", func(context *extension.RunRequestContext) { context.WorkUnitID = "" }, "work unit id"},
		{"negative iteration", func(context *extension.RunRequestContext) { context.Iteration = -1 }, "iteration"},
		{"missing binding", func(context *extension.RunRequestContext) { context.BindingID = "" }, "binding id"},
		{"missing subject digest", func(context *extension.RunRequestContext) { context.Subject.Digest = "" }, "subject digest"},
		{"wrong canonical digest", func(context *extension.RunRequestContext) {
			context.RequestDigest = digest("0")
		}, "canonical scoped context"},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := validContext()
			test.mutate(&candidate)
			if err := candidate.Validate(); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("Validate() = %v, want error containing %q", err, test.want)
			}
		})
	}

	validGate := extension.GateEvaluatorRegistration{
		Display:          extension.Display{Name: "Gate", Description: "gate check"},
		ExtensionPackage: extension.ExactPackageRef{PackageType: extension.PackageTypeExtension, PackageID: "gates", PackageVersion: "0.9.0", ArtifactDigest: digest("f")},
		ReleaseDigest:    digest("f"),
	}
	if err := validGate.Validate(); err != nil {
		t.Fatalf("valid gate: %v", err)
	}
	if validGate.ExtensionPackage.IdentityKey() != "extension/gates@0.9.0#"+digest("f") {
		t.Fatalf("gate identity = %q", validGate.ExtensionPackage.IdentityKey())
	}
	validGate.Display.Description = ""
	if err := validGate.Validate(); err == nil || !strings.Contains(err.Error(), "display description") {
		t.Fatalf("gate display: %v", err)
	}

	prerelease := validEnvelope()
	prerelease.PackageVersion = "1.4.0-rc.1+build.5"
	if err := prerelease.Validate(); err != nil {
		t.Fatalf("prerelease semver envelope: %v", err)
	}
}

func validExtension() extension.Extension {
	return extension.Extension{
		ExtensionID:    "guide-001",
		Display:        extension.Display{Name: "Guide", Description: "computational guide"},
		ExtensionPoint: extension.ExtensionPointGuideComputational,
		EffectMode:     extension.EffectModePure,
		Permissions:    []extension.Permission{extension.PermissionWorkflowRead, extension.PermissionArtifactRead},
		InputSchema:    "schema://guide-input",
		OutputSchema:   "schema://guide-output",
	}
}

package store_test

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	audit "github.com/xiaohei-info/open-agent-cluster/contracts/governance/audit"
	auditstore "github.com/xiaohei-info/open-agent-cluster/contracts/governance/audit/internal/auditstore"
)

// committedAuditStore models the platform-owned persistence adapter. It gets a
// receipt from the internal store-capability package only after loading the
// committed row; request callers never receive the receipt constructor.
type committedAuditStore struct {
	event audit.AuditEvent
}

func (store committedAuditStore) Receipt() *auditstore.Receipt {
	payload, err := json.Marshal(store.event)
	if err != nil {
		panic(err)
	}
	return auditstore.NewReceipt(store.event.ID, payload)
}

func committedAuditEvent(id string) audit.AuditEvent {
	return audit.AuditEvent{
		ID:               id,
		ScopeID:          "scope-1",
		RequestContextID: "request-context-1",
		PrincipalID:      "principal-1",
		Category:         audit.CategoryStateWrite,
		Action:           "update_project",
		Subject:          audit.SubjectKey{Kind: "project", ID: "project-1"},
		Outcome:          audit.OutcomeSucceeded,
		EvaluatedControls: []audit.EvaluatedControl{{
			Kind:          audit.ControlAuthorization,
			ControlID:     "policy-1",
			Revision:      "1",
			Digest:        "sha256:policy",
			Result:        audit.ControlResultAllowed,
			Authorization: &audit.AuthorizationControlProvenance{Decision: audit.AuthorizationControlAllow, MatchedGrant: "grant-1"},
		}},
		OccurredAt:    time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC),
		CausationID:   "cause-1",
		CorrelationID: "corr-1",
	}
}

func TestAuditDetailBindingRequiresVerifiedStoreEvent(t *testing.T) {
	positive := 0
	negative := 0
	t.Run("store-owned-positive", func(t *testing.T) {
		event := committedAuditEvent("audit-store-external")
		store := committedAuditStore{event: event}
		binding := audit.NewAuditEventStoreCapability(store.Receipt())
		loaded, err := audit.LoadAuditEventStoreBinding(context.Background(), binding, event.ID)
		if err != nil {
			t.Fatalf("LoadAuditEventStoreBinding() error = %v", err)
		}
		view, err := audit.NewAuditEventDetailViewFromStore(loaded)
		if err != nil {
			t.Fatalf("NewAuditEventDetailViewFromStore() error = %v", err)
		}
		view.RequestContextSummary = audit.RequestContextSummary{
			PrincipalID:      event.PrincipalID,
			ScopeID:          event.ScopeID,
			Source:           "api",
			RequestContextID: event.RequestContextID,
			RequestID:        "request-http-1",
			CausationID:      event.CausationID,
			CorrelationID:    event.CorrelationID,
		}
		if err := view.Validate(); err != nil {
			t.Fatalf("store-bound detail Validate() error = %v", err)
		}
		positive++
		t.Log("authority_store_binding_positive_adapter_count=1")
	})
	t.Run("whole-authority-substitution-negative", func(t *testing.T) {
		constructor := reflect.TypeOf(audit.NewAuditEventStoreCapability)
		if constructor.NumIn() != 1 || !strings.Contains(constructor.In(0).String(), "auditstore.Receipt") {
			t.Fatalf("store capability constructor accepts a caller-selectable authority: %v", constructor)
		}
		event := committedAuditEvent("audit-forged")
		payload, err := json.Marshal(event)
		if err != nil {
			t.Fatal(err)
		}
		callerReceipt := &auditstore.Receipt{EventID: event.ID, PayloadBytes: payload}
		if _, err := audit.LoadAuditEventStoreBinding(context.Background(), audit.NewAuditEventStoreCapability(callerReceipt), event.ID); err == nil {
			t.Fatal("structurally valid caller-made receipt issued a detail binding")
		}
		negative++
		t.Log("caller_authority_substitution_reject_count=1")
	})
	t.Run("zero-value-capability-negative", func(t *testing.T) {
		if _, err := audit.LoadAuditEventStoreBinding(context.Background(), audit.AuditEventStoreCapability{}, "audit-zero"); err == nil {
			t.Fatal("zero-valued store capability issued a detail binding")
		}
		negative++
		t.Log("zero_value_store_capability_reject_count=1")
	})
	t.Run("nil-receipt-capability-negative", func(t *testing.T) {
		if _, err := audit.LoadAuditEventStoreBinding(context.Background(), audit.NewAuditEventStoreCapability(nil), "audit-nil"); err == nil {
			t.Fatal("nil receipt store capability issued a detail binding")
		}
		negative++
		t.Log("nil_receipt_store_capability_reject_count=1")
	})
	t.Run("typed-nil-capability-negative", func(t *testing.T) {
		var capability *audit.AuditEventStoreCapability
		if _, err := audit.LoadAuditEventStoreBinding(context.Background(), capability, "audit-typed-nil"); err == nil {
			t.Fatal("typed nil store capability issued a detail binding")
		}
		negative++
		t.Log("typed_nil_store_capability_reject_count=1")
	})
	if positive != 1 || negative != 4 {
		t.Fatalf("authority boundary counts = positive:%d negative:%d", positive, negative)
	}
}

func TestAuditStoreCapabilityNilReceiptFailsClosed(t *testing.T) {
	var typedNil *audit.AuditEventStoreCapability
	for _, test := range []struct {
		name  string
		store audit.AuditEventStore
	}{
		{name: "zero-value", store: audit.AuditEventStoreCapability{}},
		{name: "nil-receipt", store: audit.NewAuditEventStoreCapability(nil)},
		{name: "typed-nil", store: typedNil},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := audit.LoadAuditEventStoreBinding(context.Background(), test.store, "audit-malformed"); !errors.Is(err, auditstore.ErrInvalidReceipt) {
				t.Fatalf("malformed store capability error = %v, want %v", err, auditstore.ErrInvalidReceipt)
			}
		})
	}
	t.Log("zero_value_store_capability_reject_count=1")
	t.Log("nil_receipt_store_capability_reject_count=1")
	t.Log("typed_nil_store_capability_reject_count=1")
	t.Log("malformed_store_capability_panic_count=0")
}

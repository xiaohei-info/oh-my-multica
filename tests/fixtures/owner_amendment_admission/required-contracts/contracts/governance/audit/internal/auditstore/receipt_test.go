package auditstore

import "testing"

func TestReceiptRequiresPlatformIssuedIdentityAndCopiesPayload(t *testing.T) {
	if NewReceipt("", []byte("event")) != nil || NewReceipt("event", nil) != nil {
		t.Fatal("invalid receipt inputs must not create a capability")
	}
	receipt := NewReceipt("event", []byte("payload"))
	payload, err := receipt.Payload("event")
	if err != nil || string(payload) != "payload" {
		t.Fatalf("Payload() = %q, %v", payload, err)
	}
	payload[0] = 'X'
	copyAgain, err := receipt.Payload("event")
	if err != nil || string(copyAgain) != "payload" {
		t.Fatalf("Payload() did not return a copy: %q, %v", copyAgain, err)
	}
	for _, id := range []string{"", "other"} {
		if _, err := receipt.Payload(id); err != ErrInvalidReceipt {
			t.Fatalf("Payload(%q) error = %v", id, err)
		}
	}
	invalid := &Receipt{EventID: "event", PayloadBytes: []byte("payload")}
	if _, err := invalid.Payload("event"); err != ErrInvalidReceipt {
		t.Fatalf("unmarked receipt error = %v", err)
	}
}

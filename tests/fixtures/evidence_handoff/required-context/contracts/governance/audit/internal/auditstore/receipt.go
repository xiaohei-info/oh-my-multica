// Package auditstore contains the platform-only handoff for persisted audit
// records. It is intentionally under Go's internal import boundary: callers
// outside the audit contract cannot manufacture a store receipt or choose the
// authority used to issue an AuditEvent detail binding.
package auditstore

import (
	"errors"
)

var ErrInvalidReceipt = errors.New("audit store receipt is not platform-issued")

type receiptMarker struct{}

// Receipt is a platform/store capability. The wire-shaped fields are visible
// for persistence transport, but the private marker makes a decoded or
// caller-made value unusable as a receipt.
type Receipt struct {
	EventID      string `json:"event_id"`
	PayloadBytes []byte `json:"payload"`
	marker       *receiptMarker
}

// NewReceipt is called by the platform-owned persistence adapter after it has
// loaded and verified the immutable event from its store. The returned receipt
// cannot be serialized into a usable capability or rebuilt by an external API
// caller because its marker is private and never reconstructed by decoding.
func NewReceipt(eventID string, payload []byte) *Receipt {
	if eventID == "" || len(payload) == 0 {
		return nil
	}
	return &Receipt{EventID: eventID, PayloadBytes: append([]byte(nil), payload...), marker: &receiptMarker{}}
}

func (receipt *Receipt) Payload(requestedEventID string) ([]byte, error) {
	if receipt == nil || receipt.marker == nil || receipt.EventID == "" || receipt.EventID != requestedEventID || len(receipt.PayloadBytes) == 0 {
		return nil, ErrInvalidReceipt
	}
	return append([]byte(nil), receipt.PayloadBytes...), nil
}

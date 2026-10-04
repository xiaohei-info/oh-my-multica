package audit

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func exportRequest(format ExportFormat, event AuditExportEvent) ExportAuditEventsRequest {
	return ExportAuditEventsRequest{
		RequestContext: testRequestContext(event.ScopeID, "request-export"),
		Filter:         AuditEventFilter{TimeRange: TimeRange{Start: event.OccurredAt.Add(-time.Hour), End: event.OccurredAt.Add(time.Hour)}},
		Format:         format,
	}
}

func TestAuditExportCanonicalCSVPayloadMatchesRedactedEvents(t *testing.T) {
	event := exportEvent(validAuditEvent("export-csv", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	payload, err := CanonicalAuditExportPayload(ExportFormatCSV, []AuditExportEvent{event})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(payload, []byte("audit_event_id,scope_id,project_id")) || bytes.Contains(payload, []byte("evaluated_controls")) || bytes.Contains(payload, []byte("change_summary")) {
		t.Fatalf("CSV payload crossed the redacted wire matrix: %s", payload)
	}
	stream := AuditExportStream{Format: ExportFormatCSV, Chunks: []AuditExportChunk{{Payload: payload, Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateForRequest(exportRequest(ExportFormatCSV, event), DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("canonical CSV stream rejected: %v", err)
	}
}

func TestAuditExportCanonicalJSONLPayloadMatchesRedactedEvents(t *testing.T) {
	event := exportEvent(validAuditEvent("export-jsonl", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	payload, err := CanonicalAuditExportPayload(ExportFormatJSONL, []AuditExportEvent{event})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(payload, append(encoded, '\n')) {
		t.Fatal("JSONL payload is not the canonical event encoding")
	}
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: payload, Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateForRequest(exportRequest(ExportFormatJSONL, event), DefaultQueryPolicy(), DefaultExportPolicy()); err != nil {
		t.Fatalf("canonical JSONL stream rejected: %v", err)
	}
}

func TestAuditExportRejectsMismatchedPayloadEvents(t *testing.T) {
	first := exportEvent(validAuditEvent("export-first", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	second := exportEvent(validAuditEvent("export-second", time.Date(2026, time.August, 1, 11, 0, 0, 0, time.UTC)))
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{first}), Events: []AuditExportEvent{second}}}}
	if err := stream.ValidateWithPolicy(DefaultExportPolicy()); err == nil {
		t.Fatal("payload bytes from a different event were accepted")
	}
}

func TestAuditExportRejectsUndeclaredOrSensitivePayloadFields(t *testing.T) {
	event := exportEvent(validAuditEvent("export-extra", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	canonical := exportPayload(ExportFormatJSONL, []AuditExportEvent{event})
	line := strings.TrimSuffix(string(canonical), "\n")
	for _, extra := range []string{`,"secret":"value"`, `,"api_key":"placeholder"`} {
		payload := []byte(strings.TrimSuffix(line, "}") + extra + "}\n")
		stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: payload, Events: []AuditExportEvent{event}}}}
		if err := stream.ValidateWithPolicy(DefaultExportPolicy()); err == nil {
			t.Fatalf("undeclared payload field %s was accepted", extra)
		}
	}
}

func TestAuditExportRejectsRequestStreamFormatMismatch(t *testing.T) {
	event := exportEvent(validAuditEvent("export-format", time.Date(2026, time.August, 1, 12, 0, 0, 0, time.UTC)))
	stream := AuditExportStream{Format: ExportFormatJSONL, Chunks: []AuditExportChunk{{Payload: exportPayload(ExportFormatJSONL, []AuditExportEvent{event}), Events: []AuditExportEvent{event}}}}
	if err := stream.ValidateForRequest(exportRequest(ExportFormatCSV, event), DefaultQueryPolicy(), DefaultExportPolicy()); err == nil {
		t.Fatal("request/stream format mismatch was accepted")
	}
}
